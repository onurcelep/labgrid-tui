"""Pilot tests for `labgrid-tui tour`: no network, in-memory everything,
every step reachable by keyboard alone, the footer never covered, nothing
written to the user's config.
"""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import grpc.aio
import pytest
from textual.geometry import Region
from textual.widgets import Footer, Input, Label, ListItem, ListView, Static, TabbedContent

from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.model.commands import CommandEntry
from labgrid_tui.model.identity import current_id
from labgrid_tui.tour.app import TourApp
from labgrid_tui.tour.steps import STEP_COUNT
from labgrid_tui.tour.welcome import WelcomeScreen
from labgrid_tui.ui.guidance import DIM_CLASS
from labgrid_tui.ui.screens.command_overlay import CommandOverlay, _slug
from labgrid_tui.ui.screens.coordinator_delete import CoordinatorDeleteConfirm
from labgrid_tui.ui.screens.coordinator_edit import CoordinatorEditModal
from labgrid_tui.ui.screens.coordinator_selector import CoordinatorSelector
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.status_bar import StatusBar
from labgrid_tui.ui.widgets.tour_card import TourCard

# The one top-level widget each step keeps bright, in step order.
BRIGHT_PER_STEP = (
    "main-row",
    "main-row",
    "main-row",
    "main-row",
    "activity-log",
    "main-row",
    "main-row",
    "status-bar",
)


@pytest.fixture(autouse=True)
def _no_grpc_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tour must never touch the network: proves it by making the one
    function a real connection would need blow up if ever called."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the tour must never open a gRPC channel")

    monkeypatch.setattr(grpc.aio, "insecure_channel", _boom)


async def _wait_until(pilot: object, predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        await pilot.pause()  # type: ignore[attr-defined]
        if predicate():
            return True
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)


def _card(app: TourApp) -> TourCard:
    return app.screen_stack[0].query_one(TourCard)


def _title(app: TourApp) -> str:
    return str(_card(app).border_title)


def _table_target(app: TourApp) -> Region:
    region = app.screen_stack[0].query_one(DeviceTable).cursor_row_region()
    assert region is not None
    return region


def _log_target(app: TourApp) -> Region:
    return app.screen_stack[0].query_one(ActivityLog).region


def _status_target(app: TourApp) -> Region:
    bar = app.screen_stack[0].query_one(StatusBar)
    return Region(bar.region.x, bar.region.y, min(bar.region.width, bar.first_segment_width), 1)


def _anchor(app: TourApp, bright: str) -> Region:
    """The region the step card anchors to, given the widget it spotlights."""
    if bright == "main-row":
        return _table_target(app)
    if bright == "activity-log":
        return _log_target(app)
    return _status_target(app)


def _dim_state(app: TourApp) -> dict[str, bool]:
    """Whether each top-level widget of the dashboard is dimmed, by id."""
    return {
        child.id or type(child).__name__: child.has_class(DIM_CLASS)
        for child in app.screen_stack[0].children
        if not isinstance(child, TourCard)
    }


def _card_region(app: TourApp) -> Region:
    """The step card's region as the compositor placed it."""
    screen = app.screen_stack[0]
    placed = screen._compositor.visible_widgets.get(screen.query_one(TourCard))
    assert placed is not None, "the step card is not painted"
    region, *_rest = placed
    return region


def _assert_spotlight(app: TourApp, bright: str | None) -> None:
    """Exactly *bright* keeps full colour; every other top-level widget dims."""
    state = _dim_state(app)
    if bright is None:
        assert not any(state.values()), state
        return
    assert state[bright] is False, state
    assert all(dim for name, dim in state.items() if name != bright), state


def _assert_card_anchored(app: TourApp, target: Region, size: tuple[int, int]) -> None:
    card = _card_region(app)
    assert not card.overlaps(target), (card, target)
    assert card.y == target.bottom or card.bottom == target.y, (card, target)
    assert card.x >= 0 and card.y >= 0, card
    assert card.right <= size[0] and card.bottom <= size[1], card


async def _ready(app: TourApp, pilot: object) -> None:
    """Fleet live, welcome card dismissed with Enter, step 1 card on screen."""
    assert await _wait_until(pilot, lambda: app.store.conn is ConnState.LIVE)
    assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
    assert isinstance(app.screen, WelcomeScreen)
    await pilot.press("enter")  # type: ignore[attr-defined]
    assert await _wait_until(pilot, lambda: bool(app.screen_stack[0].query(TourCard)))
    assert await _wait_until(pilot, lambda: _title(app) == "TOUR 1/8")


def _overlay_entries(overlay: CommandOverlay) -> list[CommandEntry]:
    return [entry for entries in overlay._by_category.values() for entry in entries]


def _regions(app: TourApp) -> dict[str, tuple[int, int, int, int]]:
    """(x, y, width, height) per widget id or class name, as the compositor placed them."""
    screen = app.screen_stack[0]
    out: dict[str, tuple[int, int, int, int]] = {}
    for widget, (region, *_rest) in screen._compositor.visible_widgets.items():
        out[widget.id or type(widget).__name__] = (region.x, region.y, region.width, region.height)
    return out


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


async def test_tour_starts_live_on_fake_data_with_no_network() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        assert app.sub_title == "TOUR (fake data)"
        assert app._ui_state.show_activity is True


@pytest.mark.parametrize("size", [(120, 40), (100, 30)])
async def test_welcome_card_then_a_spotlit_step_card(size: tuple[int, int]) -> None:
    app = TourApp()
    async with app.run_test(size=size) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        assert isinstance(app.screen, WelcomeScreen)
        # The welcome card is a centered modal; nothing of the tour is on the
        # dashboard yet and nothing is dimmed.
        assert not app.screen_stack[0].query(TourCard)
        assert not any(_dim_state(app).values())
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: bool(app.screen_stack[0].query(TourCard)))
        await pilot.pause()
        card = _card(app)
        assert card.can_focus is False
        assert isinstance(app.focused, DeviceTable)
        regions = _regions(app)
        card_region = regions["tour-card"]
        for other in ("Header", "status-bar", "activity-log", "Footer"):
            assert not _overlaps(card_region, regions[other]), other
        assert regions["Footer"][1] == size[1] - 1
        assert _title(app) == "TOUR 1/8"
        assert card.current_text.startswith("Every bench")
        assert "j/k" in card.current_text
        _assert_spotlight(app, "main-row")
        _assert_card_anchored(app, _table_target(app), size)


@pytest.mark.parametrize("size", [(80, 24), (60, 20), (200, 50)])
async def test_every_step_spotlights_one_widget_and_anchors_the_card(
    size: tuple[int, int],
) -> None:
    app = TourApp()
    async with app.run_test(size=size) as pilot:
        await _ready(app, pilot)
        for index, bright in enumerate(BRIGHT_PER_STEP):
            assert _title(app) == f"TOUR {index + 1}/{STEP_COUNT}"
            _assert_spotlight(app, bright)
            _assert_card_anchored(app, _anchor(app, bright), size)
            await pilot.press("n")
            await pilot.pause()
            await pilot.pause()
        assert _title(app) == "TOUR done"
        # The tour is no longer about any one widget, so the whole app is
        # bright again and the card floats free of it.
        _assert_spotlight(app, None)
        card = _card_region(app)
        assert card.right <= size[0] and card.bottom <= size[1]


async def test_moving_the_cursor_keeps_the_table_bright() -> None:
    size = (120, 30)
    app = TourApp()
    async with app.run_test(size=size) as pilot:
        await _ready(app, pilot)
        before = _dim_state(app)
        await pilot.press("j")
        await pilot.pause()
        await pilot.pause()
        assert _title(app) == "TOUR 2/8"
        assert _dim_state(app) == before
        _assert_card_anchored(app, _table_target(app), size)


async def test_welcome_n_starts_and_q_quits() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: isinstance(app.screen, WelcomeScreen))
        await pilot.press("n")
        assert await _wait_until(pilot, lambda: bool(app.screen_stack[0].query(TourCard)))
    app = TourApp()
    exit_calls: list[object] = []
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: isinstance(app.screen, WelcomeScreen))
        app.exit = lambda *a, **kw: exit_calls.append((a, kw))  # type: ignore[method-assign]
        await pilot.press("q")
        await pilot.pause()
    assert len(exit_calls) == 1


async def test_n_skips_one_step_at_a_time_and_the_done_card_stays() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        for expected in range(2, STEP_COUNT + 1):
            await pilot.press("n")
            await pilot.pause()
            assert _title(app) == f"TOUR {expected}/{STEP_COUNT}"
        await pilot.press("n")
        await pilot.pause()
        assert _title(app) == "TOUR done"
        assert _card(app).current_text.startswith("End of the tour.")
        assert not any(_dim_state(app).values())
        await pilot.press("n")  # nothing left to skip; the card stays until q
        await pilot.pause()
        assert app.screen_stack[0].query(TourCard)
        assert app.screen_stack[0].query_one(Footer)


async def test_guidance_is_mirrored_inside_modals_as_a_plain_line() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await _ready(app, pilot)
        await pilot.press("j")
        await pilot.press("r")
        await pilot.pause()
        assert _title(app) == "TOUR 3/8"
        await pilot.press("c")
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        line = overlay.query_one("#overlay-hint-tour", Static)
        assert not line.has_class("hidden")
        assert str(line.render()).startswith("TOUR 3/8: c lists")
        # Nothing of the tour is drawn over the modal: no card, no dimming.
        assert not overlay.query(TourCard)
        assert not [w for w in overlay.children if w.has_class(DIM_CLASS)]
        # No inline glyph anywhere: the tab titles are untouched.
        tabs = overlay.query_one(TabbedContent)
        categories = list(overlay._by_category)
        labels = [tabs.get_tab(f"tab-{_slug(c)}").label_text for c in categories]
        assert labels == categories
        painted = app.export_screenshot()
        assert "TOUR" in painted and "3/8" in painted
        await pilot.press("escape")
        await pilot.pause()
        # Back on the dashboard the spotlight is the current step's again.
        _assert_spotlight(app, "main-row")
        await pilot.press("n")  # to step 4: the detail step
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, DetailOverlay)
        assert not detail.query_one("#detail-hint-tour").has_class("hidden")
        title = detail.query_one("#detail-title", Static)
        assert str(title.render()).startswith("Device:")
        assert not detail.query(TourCard)
        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()
        assert _title(app) == "TOUR 5/8"
        _assert_spotlight(app, "activity-log")
        _assert_card_anchored(app, _log_target(app), (120, 40))


async def test_n_skips_even_while_coordinator_selector_is_open() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        for _ in range(STEP_COUNT - 1):
            await pilot.press("n")
            await pilot.pause()
        assert _title(app) == f"TOUR {STEP_COUNT}/{STEP_COUNT}"
        _assert_spotlight(app, "status-bar")
        _assert_card_anchored(app, _status_target(app), (120, 30))
        await pilot.press("P")
        await pilot.pause()
        selector = app.screen
        assert isinstance(selector, CoordinatorSelector)
        assert not selector.query_one("#coord-hint-tour").has_class("hidden")
        # The rows carry no glyph and no card is stacked on the modal.
        items = list(selector.query_one("#coord-list", ListView).query(ListItem))
        names = [e.name for e in selector._entries]
        desk = items[names.index("desk")].query_one(Label)
        assert str(desk.render()).startswith("desk")
        assert not selector.query(TourCard)
        await pilot.press("n")
        await pilot.pause()
        assert _title(app) == "TOUR done"
        await pilot.press("escape")
        await pilot.pause()
        assert not any(_dim_state(app).values())


async def test_full_story_by_keyboard_alone() -> None:
    app = TourApp()
    app.delayed_cue_seconds = 0.05
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        table = app.screen_stack[0].query_one(DeviceTable)
        me = current_id()
        log_lines: list[str] = []
        original = app.push_event

        def record(kind: str, subject: str, detail: str) -> None:
            log_lines.append(f"{subject} {detail}")
            original(kind, subject, detail)

        app.push_event = record  # type: ignore[method-assign]

        # 1: a real cursor move (the table's own first-row highlight does not count).
        assert _title(app) == "TOUR 1/8"
        await pilot.press("j")
        await pilot.pause()
        assert _title(app) == "TOUR 2/8"
        assert table.cursor_place() == "bench-02"

        # 2: r copies the get line; the fleet then shows the bench as mine.
        await pilot.press("r")
        await pilot.pause()
        assert _title(app) == "TOUR 3/8"
        assert await _wait_until(pilot, lambda: app.store.places["bench-02"].acquired == me)

        # 3: commands overlay on my bench: copy the highlighted entry.
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        await pilot.press("enter")  # copies and closes the overlay
        await pilot.pause()
        assert _title(app) == "TOUR 4/8"
        assert not isinstance(app.screen, CommandOverlay)

        # 4: detail open and close.
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailOverlay)
        await pilot.press("escape")
        await pilot.pause()
        assert _title(app) == "TOUR 5/8"

        # 5: entering it cued alice and the serial port; the fleet reacted.
        assert app.store.places["bench-03"].acquired == "laptop/alice"
        assert await _wait_until(
            pilot, lambda: any(line.startswith("bench-03 acquired by") for line in log_lines)
        )
        serial = app.store.resources[("rack-1", "bench-05", "serial")]
        assert serial.avail is False
        await pilot.press("n")
        await pilot.pause()
        assert _title(app) == "TOUR 6/8"

        # 6: on alice's bench r reads "Queue and acquire"; every group is
        # still listed, greyed with the reason.
        assert app.store.resources[("rack-1", "bench-05", "serial")].avail is True
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_place() == "bench-03"
        await pilot.press("c")
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        entries = _overlay_entries(overlay)
        labels = {e.template.label: e for e in entries}
        assert "Queue and acquire" in labels
        assert labels["Serial console"].reason == "held by alice"
        await pilot.press("escape")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert _title(app) == "TOUR 7/8"

        # 7: the copied line's whole effect plays out, in order: queued,
        # alice releases, allocated, acquired by me; the log tells it.
        assert await _wait_until(
            pilot, lambda: app.store.places["bench-03"].acquired == me, timeout=3.0
        )
        assert await _wait_until(
            pilot,
            lambda: any(
                r.owner == me and r.state.name == "acquired" for r in app.store.reservations
            ),
            timeout=3.0,
        )
        story = [
            next(i for i, line in enumerate(log_lines) if "tour-mine-1 waiting" in line),
            next(i for i, line in enumerate(log_lines) if line.startswith("bench-03 released")),
            next(i for i, line in enumerate(log_lines) if "tour-mine-1 -> allocated" in line),
            next(
                i
                for i, line in enumerate(log_lines)
                if line.startswith("bench-03 acquired by") and me in line
            ),
        ]
        assert story == sorted(story)
        await pilot.press("c")
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        robot = next(e for e in _overlay_entries(overlay) if e.template.category == "robot")
        await pilot.press("escape")
        await pilot.pause()
        app.runner.copy(robot)
        await pilot.pause()
        assert _title(app) == "TOUR 8/8"

        # 8: switch to desk from the selector.
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorSelector)
        await pilot.press("k")  # from lab (current) up to desk
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: set(app.store.places) == {"desk-01", "desk-02"})
        assert await _wait_until(pilot, lambda: _title(app) == "TOUR done")


async def test_q_quits_the_tour() -> None:
    app = TourApp()
    exit_calls: list[object] = []
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        app.exit = lambda *a, **kw: exit_calls.append((a, kw))  # type: ignore[method-assign]
        await pilot.press("q")
        await pilot.pause()
    assert len(exit_calls) == 1


@pytest.mark.first_run  # skip conftest's own onboarded=True seed write, see below
async def test_tour_never_touches_the_isolated_xdg_dirs() -> None:
    """Exercises every coordinator-registry write path the real app has
    (create, edit, delete, switch) plus the ui-state persist path, all
    inside the tour, then asserts nothing landed under the isolated
    XDG_CONFIG_HOME/XDG_STATE_HOME: LabgridTuiApp.persist_coordinators and
    persist_ui_state=False must absorb every one of them."""
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    state_home = Path(os.environ["XDG_STATE_HOME"])

    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        await pilot.press("j")
        await pilot.press("a")  # toggles the activity log, persisted on the real app

        # Create: "n" is shadowed by the tour's own skip binding (see
        # TourApp.BINDINGS), so drive CoordinatorSelector's create action
        # directly instead of pressing its usual key.
        await pilot.press("P")
        await pilot.pause()
        selector = app.screen
        assert isinstance(selector, CoordinatorSelector)
        selector.action_new_coordinator()
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorEditModal)
        await pilot.press(*"extra")
        await pilot.press("enter")
        await pilot.press(*"127.0.0.1:9999")
        await pilot.press("enter")
        await pilot.press("enter")  # empty prefix, submits
        await pilot.pause()
        assert "extra" in app.coordinators.entries

        # Edit: change the address of the coordinator just created.
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("up")  # entries sorted [desk, extra, lab]; land on "extra"
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorEditModal)
        address_input = app.screen.query_one("#coord-edit-address", Input)
        address_input.clear()
        await pilot.press(*"127.0.0.1:8888")
        await pilot.press("enter")
        await pilot.press("enter")
        await pilot.pause()
        assert app.coordinators.entries["extra"].address == "127.0.0.1:8888"

        # Delete: remove the entry just created and edited.
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorDeleteConfirm)
        await pilot.press("y")
        await pilot.pause()
        assert "extra" not in app.coordinators.entries

        # Switch: a real round trip to desk and back (unrelated to the
        # entry just deleted).
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("k")  # entries [desk, lab], current "lab": up to "desk"
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: set(app.store.places) == {"desk-01", "desk-02"})
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("j")  # "desk" (now active) down to "lab"
        await pilot.press("enter")
        assert await _wait_until(
            pilot, lambda: set(app.store.places) == {f"bench-0{i}" for i in range(1, 7)}
        )

    assert not config_home.exists() or not any(config_home.rglob("*"))
    assert not state_home.exists() or not any(state_home.rglob("*"))
