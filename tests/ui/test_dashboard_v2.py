import asyncio
import os
from pathlib import Path

import pytest
from textual.widgets import OptionList, Static, TabbedContent

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.packs import Pack, PackCommandTemplate
from labgrid_tui.packs import PackRegistry, PackRegistryEntry, default_packs_path, save_registry
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.uistate import state_path
from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.filter_bar import FilterBar
from tests.fake_coordinator import FakeCoordinator


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


async def _wait_places(app: LabgridTuiApp, pilot: object, n: int) -> None:
    # Wait for rendered table rows, not just store contents: the 0.2s
    # refresh flush must have run before key presses can target a row.
    for _ in range(80):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        try:
            table = app.screen.query_one(DeviceTable)
        except Exception:
            continue
        if len(app.store.places) >= n and table.row_count >= n:
            return
    raise AssertionError("places never arrived")


async def test_table_populates_and_status_bar(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", comment="bench"))
    app = LabgridTuiApp(_config(address))
    # Wide enough that every status-bar segment fits at full detail; the
    # priority-drop behavior itself is covered by test_dashboard_responsive.py.
    async with app.run_test(size=(140, 24)) as pilot:
        await _wait_places(app, pilot, 1)
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if app.screen.query_one(DeviceTable).row_count:
                break
        table = app.screen.query_one(DeviceTable)
        assert table.row_count == 1
        bar = app.screen.query_one("#status-bar", Static)
        app.screen.refresh_fleet()  # type: ignore[attr-defined]
        await pilot.pause()
        text = str(bar.render())
        assert address in text and "(from flag)" in text
        assert "1 total | 1 free | 0 reserved | 0 acquired" in text


async def test_detail_opens_modal_overlay(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DetailOverlay)
        assert app.screen.place_name == "tb-1"
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DetailOverlay)


async def test_commands_overlay_opens(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)


async def test_commands_overlay_shows_pack_tab_with_resolved_command(
    fake_coordinator: tuple[FakeCoordinator, str], tmp_path: Path
) -> None:
    pack_file = tmp_path / "robot.toml"
    pack_file.write_text(
        '[pack]\nname = "robot"\n'
        '[[commands]]\nlabel = "Smoke tests"\n'
        'command = "robot -v PLACE:{place} tests/smoke"\n'
    )
    save_registry(
        default_packs_path(os.environ),
        PackRegistry(packs={"robot": PackRegistryEntry(name="robot", source=str(pack_file))}),
    )
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    assert [pack.name for pack in app.packs] == ["robot"]
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        tabs = app.screen.query_one(TabbedContent)
        tabs.active = "tab-robot"  # raises if the pack tab does not exist
        await pilot.pause()
        options = app.screen.query_one("#overlay-list-robot", OptionList)
        assert options.option_count == 1
        option = options.get_option_at_index(0)
        assert "robot -v PLACE:tb-1 tests/smoke" in option.prompt.plain


async def test_filter_narrows_table(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    servicer.places.append(pb2.Place(name="other"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        await pilot.press("slash")
        await pilot.press("t", "b")
        await pilot.pause()
        app.screen.refresh_fleet()  # type: ignore[attr-defined]
        await pilot.pause()
        assert app.screen.query_one(DeviceTable).row_count == 1


async def test_escape_from_filter_focuses_table(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: escaping the filter left focus at None, silently
    swallowing the very next keypress instead of routing it to the table."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.pause()
        await pilot.press("slash")
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is table
        before = table.cursor_place()
        await pilot.press("j")
        await pilot.pause()
        assert table.cursor_place() != before


async def test_filter_enter_focuses_table(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        await pilot.press("slash")
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.focused is app.screen.query_one(DeviceTable)
        assert app.screen.query_one(FilterBar).has_class("hidden")


async def test_dispatch_verb_with_no_target_notifies(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    notified: list[str] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.notify = lambda message, **kwargs: notified.append(message)  # type: ignore[method-assign]
        app.screen.query_one(DeviceTable).focus()  # type: ignore[attr-defined]
        app.screen._dispatch_verb("Acquire")  # type: ignore[attr-defined]
        assert notified == ["Acquire: no target"]


async def test_dispatch_verb_ignores_pack_entry_with_colliding_label(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """A pack entry labelled "Release" must never satisfy the built-in
    Release verb dispatch (the R key, or a palette bulk action): only a
    real built-in entry may ever run for Acquire/Release."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))  # free place: no built-in Release entry exists
    app = LabgridTuiApp(_config(address))
    app.packs = [
        Pack(
            name="robot",
            description="",
            source="test",
            commands=(PackCommandTemplate(label="Release", command="echo release"),),
        )
    ]
    ran: list[str] = []
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        table = app.screen.query_one(DeviceTable)
        table.focus()
        app.screen._runner.run = (  # type: ignore[attr-defined,method-assign]
            lambda entry, **kwargs: ran.append(entry.command_line)
        )
        app.screen._dispatch_verb("Release")  # type: ignore[attr-defined]
        await pilot.pause()
        assert ran == []


async def test_escape_clears_marks(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("space")
        assert table.marks == {"tb-a"}
        await pilot.press("escape")
        await pilot.pause()
        assert table.marks == set()


async def test_escape_clears_filter_and_marks_in_one_press(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: a filter and marks both active used to need two Escape
    presses (filter first, marks on the second) instead of clearing both
    in the same keypress."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("space")
        assert table.marks == {"tb-a"}
        await pilot.press("slash")
        await pilot.press("t")
        await pilot.pause()
        filter_bar = app.screen.query_one(FilterBar)
        assert filter_bar.value == "t"
        await pilot.press("escape")
        await pilot.pause()
        assert filter_bar.value == ""
        assert filter_bar.has_class("hidden")
        assert table.marks == set()
        assert app.focused is table


async def test_escape_with_filter_and_marks_refreshes_exactly_once(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: _close_filter clearing FilterBar.value (which reaches
    DeviceTable.filter_query only asynchronously via FilterChanged) and
    _clear_marks refreshing synchronously right after used to each trigger
    their own refresh_fleet() call: the first rendered with the filter
    still applied. One Escape must yield exactly one refresh, already
    showing the fully cleared state."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("space")
        assert table.marks == {"tb-a"}
        await pilot.press("slash")
        await pilot.press("t", "b", "-", "a")
        await pilot.pause()
        assert table.row_count == 1  # narrowed to tb-a only

        calls = 0
        original_refresh = app.screen.refresh_fleet  # type: ignore[attr-defined]

        def counting_refresh() -> None:
            nonlocal calls
            calls += 1
            original_refresh()

        app.screen.refresh_fleet = counting_refresh  # type: ignore[method-assign]

        await pilot.press("escape")
        await pilot.pause()
        await pilot.pause()  # let any deferred FilterChanged catch up too

        assert calls == 1
        assert table.marks == set()
        assert app.screen.query_one(FilterBar).value == ""
        assert table.row_count == 2  # filter cleared: both places shown again


async def test_bulk_dispatch_clears_marks(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        table = app.screen.query_one(DeviceTable)
        table.marks.update({"tb-a", "tb-b"})
        table.focus()
        await pilot.press("r")  # both free -> both Acquire dispatched
        await pilot.pause()
        assert table.marks == set()


async def test_status_bar_shows_marked_count(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        bar = app.screen.query_one("#status-bar", Static)
        assert "marked" not in str(bar.render())
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("space")
        await pilot.pause()
        assert "marked: 1" in str(bar.render())


async def test_acquire_refused_when_exporter_fully_offline(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    place = pb2.Place(name="tb-1")
    place.matches.add(exporter="exp1", group="g1", cls="*")
    servicer.places.append(place)
    resource = pb2.Resource(cls="NetworkPowerPort", avail=False)
    resource.path.exporter_name = "exp1"
    resource.path.group_name = "g1"
    resource.path.resource_name = "power"
    servicer.resources.append(resource)
    app = LabgridTuiApp(_config(address))
    notified: list[str] = []
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        app.notify = lambda message, **kwargs: notified.append(message)  # type: ignore[method-assign]
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("r")
        await pilot.pause()
        assert notified == ["all resources offline"]


async def test_toggle_activity_persists_ui_state(
    fake_coordinator: tuple[FakeCoordinator, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("a")
        await pilot.pause()
    path = state_path(os.environ)
    assert path.exists()
    assert "show_activity = true" in path.read_text()


async def test_tab_cycles_between_table_and_activity_log(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """FilterBar sits first in compose order but stays hidden by default;
    a hidden widget must not enter the Tab cycle, so Tab from the table
    goes straight to the activity log and back, a two-widget cycle."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("a")  # reveal the activity log; hidden by default
        await pilot.pause()
        assert app.focused is table

        await pilot.press("tab")
        await pilot.pause()
        log = app.screen.query_one(ActivityLog)
        assert app.focused is log

        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is table

        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.focused is log


async def test_hidden_filter_bar_excluded_from_tab_cycle(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """FilterBar composes first, ahead of the table; if a hidden widget
    stayed in the Tab cycle, Tab from the table (with the log also hidden,
    the default state) would land on it instead of staying put."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.pause()
        assert app.screen.query_one(ActivityLog).has_class("hidden")

        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is table
        assert not isinstance(app.focused, FilterBar)


async def test_escape_from_activity_log_focuses_table(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("a")  # reveal the activity log; hidden by default
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.focused is app.screen.query_one(ActivityLog)

        await pilot.press("escape")
        await pilot.pause()
        assert app.focused is table


async def _count_refreshes(app: LabgridTuiApp) -> list[int]:
    calls = [0]
    original = app.screen.refresh_fleet  # type: ignore[attr-defined]

    def counting() -> None:
        calls[0] += 1
        original()

    app.screen.refresh_fleet = counting  # type: ignore[attr-defined]
    return calls


async def test_escape_with_filter_only_refreshes_once_and_restores_rows(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    # No marks: the re-render must come from an explicit refresh, not from
    # the layout change of hiding the filter bar.
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="zz-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        table = app.screen.query_one(DeviceTable)
        table.focus()
        await pilot.press("slash")
        await pilot.press("z", "z")
        await pilot.pause()
        assert table.row_count == 1
        calls = await _count_refreshes(app)
        await pilot.press("escape")
        assert calls[0] == 1
        assert table.row_count == 2
        await pilot.pause()
        assert calls[0] == 1


async def test_enter_in_filter_refreshes_once_and_restores_rows(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="zz-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _wait_places(app, pilot, 2)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("slash")
        await pilot.press("z", "z")
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        assert table.row_count == 1
        calls = await _count_refreshes(app)
        await pilot.press("enter")
        assert calls[0] == 1
        assert table.row_count == 2
        assert app.focused is table
