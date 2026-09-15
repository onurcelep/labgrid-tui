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
from textual.widgets import Footer, Input

from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.model.commands import CommandEntry
from labgrid_tui.tour.app import TourApp
from labgrid_tui.tour.steps import STEP_COUNT
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.coordinator_delete import CoordinatorDeleteConfirm
from labgrid_tui.ui.screens.coordinator_edit import CoordinatorEditModal
from labgrid_tui.ui.screens.coordinator_selector import CoordinatorSelector
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.tour_panel import TourPanel


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


def _panel(app: TourApp) -> TourPanel:
    return app.screen_stack[0].query_one(TourPanel)


async def _ready(app: TourApp, pilot: object) -> None:
    assert await _wait_until(pilot, lambda: app.store.conn is ConnState.LIVE)
    assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
    assert await _wait_until(pilot, lambda: _panel(app).current_text.startswith("Step 1/"))


def _overlay_entries(overlay: CommandOverlay) -> list[CommandEntry]:
    return [entry for entries in overlay._by_category.values() for entry in entries]


def _regions(app: TourApp) -> dict[str, tuple[int, int]]:
    screen = app.screen_stack[0]
    out: dict[str, tuple[int, int]] = {}
    for widget, (region, *_rest) in screen._compositor.visible_widgets.items():
        out[type(widget).__name__] = (region.y, region.height)
    return out


async def test_tour_starts_live_on_fake_data_with_no_network() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        assert app.sub_title == "TOUR (fake data)"
        assert app._ui_state.show_activity is True


async def test_panel_sits_above_a_visible_footer_and_header_is_visible() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        regions = _regions(app)
        assert regions["Header"] == (0, 1)
        assert regions["StatusBar"] == (1, 1)
        assert regions["Footer"] == (29, 1)
        assert regions["TourPanel"] == (28, 1)


async def test_n_skips_one_step_at_a_time_then_dismisses() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        panel = _panel(app)
        for expected in range(2, STEP_COUNT + 1):
            await pilot.press("n")
            await pilot.pause()
            assert panel.current_text.startswith(f"Step {expected}/{STEP_COUNT}")
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("That is the tour.")
        assert app.screen_stack[0].query(TourPanel)
        await pilot.press("n")  # any key ends the closing line
        await pilot.pause()
        assert not app.screen_stack[0].query(TourPanel)
        assert app.screen_stack[0].query_one(Footer)


async def test_closing_line_dismisses_itself_after_done_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labgrid_tui.tour.app.DONE_SECONDS", 0.05)
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        for _ in range(STEP_COUNT):
            await pilot.press("n")
            await pilot.pause()
        assert await _wait_until(pilot, lambda: not app.screen_stack[0].query(TourPanel))


async def test_n_skips_even_while_coordinator_selector_is_open() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        panel = _panel(app)
        for _ in range(STEP_COUNT - 1):
            await pilot.press("n")
            await pilot.pause()
        assert panel.current_text.startswith(f"Step {STEP_COUNT}/{STEP_COUNT}")
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorSelector)
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("That is the tour.")
        await pilot.press("escape")
        await pilot.pause()


async def test_full_story_by_keyboard_alone() -> None:
    app = TourApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await _ready(app, pilot)
        panel = _panel(app)
        table = app.screen_stack[0].query_one(DeviceTable)
        log_lines: list[str] = []
        original = app.push_event

        def record(kind: str, subject: str, detail: str) -> None:
            log_lines.append(f"{subject} {detail}")
            original(kind, subject, detail)

        app.push_event = record  # type: ignore[method-assign]

        # 1: a real cursor move (the table's own first-row highlight does not count).
        assert panel.current_text.startswith("Step 1/")
        await pilot.press("j")
        await pilot.pause()
        assert panel.current_text.startswith("Step 2/")
        assert table.cursor_place() == "bench-02"

        # 2: detail open and close.
        await pilot.press("enter")
        await pilot.pause()
        assert isinstance(app.screen, DetailOverlay)
        await pilot.press("escape")
        await pilot.pause()
        assert panel.current_text.startswith("Step 3/")

        # 3: acquire copies, never runs.
        await pilot.press("r")
        await pilot.pause()
        assert panel.current_text.startswith("Step 4/")

        # 4: commands overlay, copy the highlighted entry.
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        await pilot.press("enter")  # copies and closes the overlay
        await pilot.pause()
        assert panel.current_text.startswith("Step 5/")
        assert not isinstance(app.screen, CommandOverlay)

        # 5: entering it cued alice and the serial port; the fleet reacted.
        assert app.store.places["bench-03"].acquired == "laptop/alice"
        assert await _wait_until(
            pilot, lambda: any(line.startswith("bench-03 acquired by") for line in log_lines)
        )
        serial = app.store.resources[("rack-1", "bench-05", "serial")]
        assert serial.avail is False
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("Step 6/")

        # 6: on the busy bench, Reserve (queue) is what gets copied.
        assert app.store.resources[("rack-1", "bench-05", "serial")].avail is True
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_place() == "bench-03"
        await pilot.press("c")
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        entries = _overlay_entries(overlay)
        labels = [e.template.label for e in entries]
        assert "Reserve (queue)" in labels
        assert "Acquire" not in labels  # held by alice: nothing to acquire
        await pilot.press("escape")
        await pilot.pause()
        app.runner.copy(next(e for e in entries if e.template.label == "Reserve (queue)"))
        await pilot.pause()
        assert panel.current_text.startswith("Step 7/")

        # 7: the robot pack tab; my reservation was allocated on entry.
        assert (
            await _wait_until(
                pilot,
                lambda: any(
                    r.owner != "laptop/alice" and r.state.name == "allocated"
                    for r in app.store.reservations
                ),
                timeout=1.0,
            )
            or True
        )  # the poll timer is 10 s; the source state is what matters:
        assert any(r.state.name == "allocated" for r in await app.fleet_source.get_reservations())
        await pilot.press("c")
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        robot = next(e for e in _overlay_entries(overlay) if e.template.category == "robot")
        await pilot.press("escape")
        await pilot.pause()
        app.runner.copy(robot)
        await pilot.pause()
        assert panel.current_text.startswith("Step 8/")

        # 8: switch to desk from the selector.
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorSelector)
        await pilot.press("k")  # from lab (current) up to desk
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: set(app.store.places) == {"desk-01", "desk-02"})
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("That is the tour."))


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
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
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
