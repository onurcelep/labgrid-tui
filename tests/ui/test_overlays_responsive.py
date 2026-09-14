"""Overlay fit at every breakpoint size: modal stays within the screen and
its scrollable content never needs a horizontal scrollbar.

Sizes mirror the brief's parameter set: (60,12), (80,24), (100,50),
(140,45), (200,60).
"""

import pytest
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static, Tab, TabbedContent, TabPane, Tabs

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.layout import HORIZONTAL_BREAKPOINTS, VERTICAL_BREAKPOINTS
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.screens.help_overlay import (
    _FULL_HINT,
    _FULL_TAB_TITLES,
    _NARROW_HINT,
    _NARROW_TAB_TITLES,
    HelpOverlay,
)
from labgrid_tui.ui.widgets.device_table import DeviceTable
from tests.fake_coordinator import FakeCoordinator

SIZES = [(60, 12), (80, 24), (100, 50), (140, 45), (200, 60)]


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


def _assert_fits(overlay: ModalScreen[object], modal_id: str) -> None:
    screen_region = overlay.region
    modal = overlay.query_one(f"#{modal_id}")
    region = modal.region
    assert region.right <= screen_region.right, (region, screen_region)
    assert region.bottom <= screen_region.bottom, (region, screen_region)


def _assert_no_horizontal_scroll(scroller: VerticalScroll | OptionList) -> None:
    assert scroller.scroll_x == 0
    assert scroller.virtual_size.width <= scroller.size.width


class _HelpHarness(App[None]):
    # Real HORIZONTAL_BREAKPOINTS/VERTICAL_BREAKPOINTS, matching
    # LabgridTuiApp, so -narrow/-short actually engage in these tests.
    HORIZONTAL_BREAKPOINTS = HORIZONTAL_BREAKPOINTS
    VERTICAL_BREAKPOINTS = VERTICAL_BREAKPOINTS

    def compose(self) -> ComposeResult:
        yield Static("base")


@pytest.mark.parametrize("size", SIZES)
async def test_help_overlay_fits_and_does_not_scroll_horizontally(
    size: tuple[int, int],
) -> None:
    app = _HelpHarness()
    async with app.run_test(size=size) as pilot:
        app.push_screen(HelpOverlay())
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, HelpOverlay)
        _assert_fits(overlay, "help-modal")

        tabbed = overlay.query_one(TabbedContent)
        pane_ids = [pane.id for pane in overlay.query(TabPane) if pane.id]
        assert pane_ids  # sanity: tabs actually composed
        for pane_id in pane_ids:
            tabbed.active = pane_id
            await pilot.pause()
            pane = tabbed.active_pane
            assert pane is not None
            scroll = pane.query_one(VerticalScroll)
            _assert_no_horizontal_scroll(scroll)


async def test_help_overlay_narrow_layout_applies_on_cold_open() -> None:
    """A freshly pushed ModalScreen gets no resize event of its own before
    its on_mount runs: narrow titles/hint must be correct immediately,
    not only after a subsequent resize.
    """
    app = _HelpHarness()
    async with app.run_test(size=(60, 24)) as pilot:
        app.push_screen(HelpOverlay())
        await pilot.pause()  # cold open only: no resize since the push
        overlay = app.screen
        assert isinstance(overlay, HelpOverlay)

        tabs = list(overlay.query_one(TabbedContent).query_one(Tabs).query(Tab))
        assert [tab.label_text for tab in tabs] == list(_NARROW_TAB_TITLES)
        assert overlay.query_one("#help-hint", Static).content == _NARROW_HINT

        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        tabs = list(overlay.query_one(TabbedContent).query_one(Tabs).query(Tab))
        assert [tab.label_text for tab in tabs] == list(_FULL_TAB_TITLES)
        assert overlay.query_one("#help-hint", Static).content == _FULL_HINT


def _place_with_serial(name: str = "tb-1") -> pb2.Place:
    place = pb2.Place(name=name)
    place.matches.add(exporter="exp1", group="g1", cls="*")
    return place


def _serial_resource(exporter: str = "exp1") -> pb2.Resource:
    resource = pb2.Resource(cls="NetworkSerialPort", avail=True)
    resource.path.exporter_name = exporter
    resource.path.group_name = "g1"
    resource.path.resource_name = "serial"
    return resource


async def _wait_places(app: LabgridTuiApp, pilot: object, n: int) -> None:
    import asyncio

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


@pytest.mark.parametrize("size", SIZES)
async def test_detail_overlay_fits_and_does_not_scroll_horizontally(
    fake_coordinator: tuple[FakeCoordinator, str], size: tuple[int, int]
) -> None:
    servicer, address = fake_coordinator
    place = _place_with_serial("bench-with-a-rather-long-name")
    place.comment = "a very long comment " * 5
    servicer.places.append(place)
    servicer.resources.append(_serial_resource())
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=size) as pilot:
        await _wait_places(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("d")
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, DetailOverlay)
        _assert_fits(overlay, "detail-modal")
        scroll = overlay.query_one("#detail-scroll", VerticalScroll)
        _assert_no_horizontal_scroll(scroll)


async def test_detail_overlay_narrow_layout_applies_on_cold_open(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Same cold-open requirement as the help overlay: the resource-name/
    status split must apply on the very first render, not just after a
    later resize.
    """
    servicer, address = fake_coordinator
    place = _place_with_serial("tb-narrow-resource-check")
    servicer.places.append(place)
    servicer.resources.append(_serial_resource())
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(60, 24)) as pilot:
        await _wait_places(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("d")
        await pilot.pause()  # cold open only: no resize since the push
        overlay = app.screen
        assert isinstance(overlay, DetailOverlay)

        def _resource_lines() -> list[str]:
            body = overlay.query_one("#detail-body", Static)
            return body.content.plain.splitlines()  # type: ignore[union-attr]

        lines = _resource_lines()
        name_line = next(i for i, line in enumerate(lines) if "serial" in line)
        assert "(online)" not in lines[name_line]
        assert "(online)" in lines[name_line + 1]

        await pilot.resize_terminal(120, 40)
        await pilot.pause()
        lines = _resource_lines()
        name_line = next(i for i, line in enumerate(lines) if "serial" in line)
        assert "(online)" in lines[name_line]


def _entries() -> list[CommandEntry]:
    long_suffix = "acquire --very-long-flag-name-to-force-wrapping=yes-really-long"
    template = CommandTemplate("Manage", "Acquire", long_suffix)
    entry = CommandEntry(
        template,
        f"labgrid-client -p bench-14 {long_suffix}",
        EntryState.RUNNABLE,
        None,
    )
    return [entry]


class _CommandHarness(App[None]):
    HORIZONTAL_BREAKPOINTS = HORIZONTAL_BREAKPOINTS
    VERTICAL_BREAKPOINTS = VERTICAL_BREAKPOINTS

    def compose(self) -> ComposeResult:
        yield Static("base")

    def push_activity(self, _line: str) -> None:
        pass


class _Runner:
    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        pass

    def copy(self, entry: CommandEntry) -> None:
        pass


@pytest.mark.parametrize("size", SIZES)
async def test_command_overlay_fits_and_does_not_scroll_horizontally(
    size: tuple[int, int],
) -> None:
    app = _CommandHarness()
    async with app.run_test(size=size) as pilot:
        app.push_screen(CommandOverlay("tb-1", _entries(), _Runner()))
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        _assert_fits(overlay, "overlay-body")

        options = overlay.query_one(OptionList)
        _assert_no_horizontal_scroll(options)
