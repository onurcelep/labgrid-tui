import asyncio
from typing import Any

import pytest
from rich.text import Text
from textual.app import App, ComposeResult
from textual.widgets import Input, OptionList, Static, TabbedContent, TabPane
from textual.widgets.option_list import Option

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState
from labgrid_tui.model.identity import current_id
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.widgets.device_table import DeviceTable
from tests.fake_coordinator import FakeCoordinator


class _Runner:
    def __init__(self) -> None:
        self.ran: list[CommandEntry] = []
        self.copied: list[CommandEntry] = []

    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        self.ran.append(entry)

    def copy(self, entry: CommandEntry) -> None:
        self.copied.append(entry)


def _make(category: str, label: str, suffix: str,
          state: EntryState = EntryState.RUNNABLE,
          reason: str | None = None) -> CommandEntry:
    template = CommandTemplate(category, label, suffix)
    return CommandEntry(template, f"labgrid-client -p tb-1 {suffix}", state, reason)


def _entries() -> list[CommandEntry]:
    return [
        _make("Manage", "Show", "show"),
        _make("Manage", "Release", "release", EntryState.UNAVAILABLE,
              "requires acquire"),
        _make("Power", "Power on", "power on"),
        _make("Power", "Power off", "power off"),
        _make("Connect", "Console", "console"),
    ]


class _Harness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.activity: list[str] = []

    def compose(self) -> ComposeResult:
        yield Static("base")

    def push_activity(self, line: str) -> None:
        self.activity.append(line)


def _option_text(options: OptionList, index: int) -> Text:
    option: Option = options.get_option_at_index(index)
    prompt = option.prompt
    assert isinstance(prompt, Text)
    return prompt


async def test_tab_per_category_and_first_active() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        tabs = app.screen.query_one(TabbedContent)
        assert tabs.tab_count == 3
        # tabs follow GROUP_ORDER: Connect, Power, Manage
        assert tabs.active == "tab-connect"
        active_list = app.screen.query_one("#overlay-list-connect", OptionList)
        assert active_list.option_count == 1
        assert app.focused is active_list
        assert app.screen.query_one("#overlay-list-manage", OptionList).option_count == 2


async def test_category_param_activates_tab() -> None:
    app = _Harness()
    runner = _Runner()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), runner,
                                       category="Power"))
        await pilot.pause()
        assert app.screen.query_one(TabbedContent).active == "tab-power"
        await pilot.press("enter")
        await pilot.pause()
        # Enter copies: it never runs.
        assert [e.template.label for e in runner.copied] == ["Power on"]
        assert runner.ran == []
        assert not isinstance(app.screen, CommandOverlay)


async def test_left_right_switch_tabs_and_focus_follows() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        tabs = app.screen.query_one(TabbedContent)
        assert tabs.active == "tab-power"
        assert app.focused is app.screen.query_one("#overlay-list-power", OptionList)
        await pilot.press("left")
        await pilot.pause()
        assert tabs.active == "tab-connect"


async def test_tab_shift_tab_switch_tabs_like_left_right() -> None:
    """Tab/Shift+Tab used to cycle focus between the filter input and the
    option list; the key-forwarding design makes that unnecessary, so they
    are reassigned to the same tab-switching left/right already do."""
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        tabs = app.screen.query_one(TabbedContent)
        assert tabs.active == "tab-connect"

        await pilot.press("tab")
        await pilot.pause()
        assert tabs.active == "tab-power"
        assert app.focused is app.screen.query_one("#overlay-list-power", OptionList)

        await pilot.press("shift+tab")
        await pilot.pause()
        assert tabs.active == "tab-connect"


async def test_ctrl_n_moves_option_list_cursor_down() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner(),
                                       category="Manage"))
        await pilot.pause()
        options = app.screen.query_one("#overlay-list-manage", OptionList)
        assert options.option_count == 2
        assert options.highlighted == 0
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert options.highlighted == 1


async def test_filter_narrows_within_tabs() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner(),
                                       category="Power"))
        await pilot.pause()
        await pilot.press("o", "f", "f")
        await pilot.pause()
        assert app.screen.query_one(Input).value == "off"
        assert app.screen.query_one("#overlay-list-power", OptionList).option_count == 1
        # other tabs filtered too
        assert app.screen.query_one("#overlay-list-connect", OptionList).option_count == 0


async def test_enter_on_unavailable_copies() -> None:
    app = _Harness()
    runner = _Runner()
    entries = [_make("Manage", "Release", "release", EntryState.UNAVAILABLE,
                     "requires acquire")]
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", entries, runner))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert [e.template.label for e in runner.copied] == ["Release"]
        assert runner.ran == []


async def test_y_copies_from_active_tab() -> None:
    app = _Harness()
    runner = _Runner()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), runner,
                                       category="Connect"))
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
        assert [e.template.label for e in runner.copied] == ["Console"]
        assert not isinstance(app.screen, CommandOverlay)


def _pack_entry(
    label: str, command_line: str,
    state: EntryState = EntryState.RUNNABLE, reason: str | None = None,
) -> CommandEntry:
    template = CommandTemplate("robot", label, command_line, copy_only=True)
    return CommandEntry(template, command_line, state, reason)


async def test_pack_tab_appears_after_builtin_tabs_in_registration_order() -> None:
    entries = [
        *_entries(),
        _pack_entry("Smoke tests", "robot -v PLACE:tb-1 tests/smoke"),
    ]
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", entries, _Runner()))
        await pilot.pause()
        tabs = app.screen.query_one(TabbedContent)
        # Built-in categories (Manage/Power/Connect, in GROUP_ORDER) first;
        # the pack tab, an unknown category, follows, keeping the order
        # entries arrived in (see GROUP_ORDER's unknown-category handling).
        assert tabs.tab_count == 4
        pack_list = app.screen.query_one("#overlay-list-robot", OptionList)
        assert pack_list.option_count == 1


async def test_pack_entry_unavailable_shows_reason_and_struck_command() -> None:
    entries = [
        _pack_entry(
            "Needs network", "robot -v IP:{res.NetworkService.address} tests/net",
            EntryState.UNAVAILABLE, "needs NetworkService",
        ),
    ]
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", entries, _Runner()))
        await pilot.pause()
        options = app.screen.query_one("#overlay-list-robot", OptionList)
        text = _option_text(options, 0)
        assert "needs NetworkService" in text.plain
        assert "dim strike" in str(text.spans)


async def test_pack_entry_enter_copies_and_never_runs() -> None:
    """A pack entry's Enter path is identical to every other entry's:
    the overlay only ever calls runner.copy(), but this pins it
    explicitly since pack entries must never execute (see
    ui.actions.CliActionRunner.run's copy_only guard)."""
    entries = [_pack_entry("Smoke tests", "robot -v PLACE:tb-1 tests/smoke")]
    app = _Harness()
    runner = _Runner()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", entries, runner))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert [e.template.label for e in runner.copied] == ["Smoke tests"]
        assert runner.copied[0].template.copy_only is True
        assert runner.ran == []


async def test_escape_dismisses() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandOverlay)


async def test_q_and_c_close_overlay() -> None:
    """q/c dismiss the overlay via a binding rather than landing in the
    filter input: proof they were excluded from on_key's forwarding."""
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, CommandOverlay)

    app2 = _Harness()
    async with app2.run_test() as pilot:
        app2.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        assert not isinstance(app2.screen, CommandOverlay)


async def test_shift_enter_copies_via_suspend(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_copy_via_suspend(app: Any, text: str, *, label: str = "text") -> None:
        calls.append((text, label))

    monkeypatch.setattr(
        "labgrid_tui.ui.screens.command_overlay.copy_via_suspend",
        fake_copy_via_suspend,
    )
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner(),
                                       category="Connect"))
        await pilot.pause()
        await pilot.press("shift+enter")
        await pilot.pause()
        assert calls == [("labgrid-client -p tb-1 console", "command")]
        assert not isinstance(app.screen, CommandOverlay)
        # Shift+Enter must log to the activity feed too, like the detail
        # overlay's equivalent action: the OSC 52 path already does via
        # the runner, so the suspend fallback should not be silent.
        assert app.activity == ["copied: labgrid-client -p tb-1 console"]


async def test_list_items_are_two_line_label_and_command() -> None:
    """Each row shows a bold label line and a dim command line beneath it:
    no separate preview line is needed."""
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        options = app.screen.query_one("#overlay-list-connect", OptionList)
        text = _option_text(options, 0)
        lines = text.plain.splitlines()
        assert lines[0] == "Console"
        assert lines[1].strip() == "labgrid-client -p tb-1 console"


async def test_unavailable_item_is_dim_and_struck() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        options = app.screen.query_one("#overlay-list-manage", OptionList)
        text = _option_text(options, 1)  # Release, unavailable
        lines = text.plain.splitlines()
        assert lines[0] == "Release  (requires acquire)"
        assert lines[1].strip() == "labgrid-client -p tb-1 release"
        # Command line carries a strike style for unavailable entries.
        assert any("strike" in str(span.style) for span in text.spans)


async def test_no_preview_widget() -> None:
    """The single highlighted-row preview is redundant once every row
    already shows its full command line: it must be gone."""
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        assert len(app.screen.query("#overlay-preview")) == 0
        hint = str(app.screen.query_one("#overlay-hint", Static).render())
        assert "Enter" in hint and "Shift+Enter" in hint and "Ctrl+E" in hint


async def test_ctrl_e_edits_and_copies_edited_line() -> None:
    app = _Harness()
    runner = _Runner()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), runner,
                                       category="Power"))
        await pilot.pause()
        await pilot.press("ctrl+e")
        await pilot.pause()
        edit = app.screen.query_one("#overlay-edit", Input)
        assert not edit.has_class("hidden")
        assert edit.value == "labgrid-client -p tb-1 power on"
        hint = str(app.screen.query_one("#overlay-edit-hint", Static).render())
        assert "--delay" in hint  # curated power flags surfaced
        edit.value = "labgrid-client -p tb-1 power cycle -t 3"
        await pilot.press("enter")
        await pilot.pause()
        assert runner.copied[-1].command_line == "labgrid-client -p tb-1 power cycle -t 3"
        assert runner.ran == []
        assert not isinstance(app.screen, CommandOverlay)


async def test_edit_escape_cancels_without_closing_overlay() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        await pilot.press("ctrl+e")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        assert app.screen.query_one("#overlay-edit", Input).has_class("hidden")
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandOverlay)


async def test_edited_placeholder_semantics_are_preserved_on_copy() -> None:
    """The overlay hands edited lines to the runner with needs_args set by
    whether <placeholders> remain, so a downstream ActionRunner can still
    tell an unfilled template apart from a filled-in one, even though both
    now route through copy()."""
    app = _Harness()
    runner = _Runner()
    template = CommandTemplate("Manage", "Allow user", "allow <host>/<user>",
                               needs_args=True)
    entries = [CommandEntry(template,
                            "labgrid-client -p tb-1 allow <host>/<user>",
                            EntryState.RUNNABLE, None)]
    async with app.run_test() as pilot:
        app.push_screen(CommandOverlay("tb-1", entries, runner))
        await pilot.pause()
        await pilot.press("ctrl+e")
        await pilot.pause()
        await pilot.press("enter")  # unedited: placeholder still present
        await pilot.pause()
        assert runner.copied[-1].template.needs_args is True

    app2 = _Harness()
    runner2 = _Runner()
    async with app2.run_test() as pilot:
        app2.push_screen(CommandOverlay("tb-1", list(entries), runner2))
        await pilot.pause()
        await pilot.press("ctrl+e")
        await pilot.pause()
        edit = app2.screen.query_one("#overlay-edit", Input)
        edit.value = "labgrid-client -p tb-1 allow h/u"
        await pilot.press("enter")
        await pilot.pause()
        assert runner2.copied[-1].template.needs_args is False
        assert runner2.copied[-1].command_line.endswith("allow h/u")
        assert runner2.ran == []


# ----------------------------------------------------------------------
# Reservations tab: driven through the real app (dashboard -> evaluate()),
# since the tab's presence depends on store.reservations, not on anything
# CommandOverlay itself computes.
# ----------------------------------------------------------------------


def _config(address: str) -> Config:
    return Config(coordinator=address, coordinator_source="flag", prefix=None,
                  capability_overrides={}, proxy_set=False)


async def _ready(app: LabgridTuiApp, pilot: object, n: int) -> DeviceTable:
    for _ in range(80):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        table = app.screen.query_one(DeviceTable)
        if table.row_count >= n:
            return table
    raise AssertionError("table never populated")


def _tab_ids(app: LabgridTuiApp) -> list[str | None]:
    return [pane.id for pane in app.screen.query(TabPane)]


async def test_reservations_tab_present_with_my_reservations(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    servicer.reservations.append(pb2.Reservation(owner=current_id(), token="TOK", state=0))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        await app._poll_reservations()
        table.focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        assert "tab-reservations" in _tab_ids(app)
        options = app.screen.query_one("#overlay-list-reservations", OptionList)
        assert options.option_count == 2  # cancel + acquire-allocated-place


async def test_reservations_tab_absent_without_reservations(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        await app._poll_reservations()
        table.focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        assert "tab-reservations" not in _tab_ids(app)


async def test_reservations_tab_ignores_other_users_reservations(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    servicer.reservations.append(pb2.Reservation(owner="host9/carol", token="TOK", state=0))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        await app._poll_reservations()
        table.focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        assert "tab-reservations" not in _tab_ids(app)
