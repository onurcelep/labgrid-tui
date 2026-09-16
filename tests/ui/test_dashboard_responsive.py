"""Responsive-layout tests: breakpoint sizes, too-small notice, status bar
priority-dropping, and table column priority-dropping.

Sizes mirror the brief's parameter set: (60,12), (80,24), (100,50),
(140,45), (200,60).
"""

import asyncio

import pytest
from textual.widgets import Static

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.layout import MIN_HEIGHT, MIN_WIDTH
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


async def _wait_places(app: LabgridTuiApp, pilot: object, n: int) -> None:
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


def _fleet(servicer: FakeCoordinator, count: int = 8) -> None:
    for i in range(count):
        place = pb2.Place(
            name=f"bench-{i:02d}",
            comment=f"bench {i} in rack A, very long comment describing the setup",
            tags={"site": "lab1", "rack": "a", "owner": f"user{i}"},
        )
        place.matches.add(exporter="*", group="g1", cls="*")
        servicer.places.append(place)
        resource = pb2.Resource(cls="NetworkSerialPort", avail=True)
        resource.path.exporter_name = f"exp{i}"
        resource.path.group_name = "g1"
        resource.path.resource_name = "serial"
        servicer.resources.append(resource)


@pytest.mark.parametrize("size", SIZES)
async def test_no_widget_region_exceeds_screen(
    fake_coordinator: tuple[FakeCoordinator, str], size: tuple[int, int]
) -> None:
    """The dashboard's own top-level widgets must stay within the screen.

    Restricted to this package's own widgets (not every Textual-internal
    descendant, e.g. Footer's individual FooterKey children): Footer lays
    those out and clips overflow internally as part of its own compacting
    behavior, which is out of scope here: see the brief's "Textual's
    Footer already compacts" note.
    """
    servicer, address = fake_coordinator
    _fleet(servicer)
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=size) as pilot:
        await _wait_places(app, pilot, 8)
        await pilot.pause()
        screen_region = app.screen.region
        for selector in (
            "#status-bar",
            "#filter-bar",
            "#main-row",
            "#fleet-table",
            "#activity-log",
            "#too-small-notice",
            "Header",
            "Footer",
        ):
            widget = app.screen.query_one(selector)
            region = widget.region
            if region.width == 0 or region.height == 0:
                continue  # hidden (display: none) at this size
            assert region.right <= screen_region.right, (selector, region, screen_region)
            assert region.bottom <= screen_region.bottom, (selector, region, screen_region)


@pytest.mark.parametrize("size", SIZES)
async def test_status_bar_never_exceeds_width(
    fake_coordinator: tuple[FakeCoordinator, str], size: tuple[int, int]
) -> None:
    servicer, address = fake_coordinator
    _fleet(servicer)
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=size) as pilot:
        await _wait_places(app, pilot, 8)
        app.screen.refresh_fleet()  # type: ignore[attr-defined]
        await pilot.pause()
        # The status bar stays dock:top even when the too-small notice
        # replaces #main-row, so this check applies at every size.
        bar = app.screen.query_one("#status-bar", Static)
        text = str(bar.render())
        assert len(text) <= bar.content_size.width


@pytest.mark.parametrize("size", [(60, 24), (80, 24), (120, 24), (160, 24)])
async def test_table_columns_by_width(
    fake_coordinator: tuple[FakeCoordinator, str], size: tuple[int, int]
) -> None:
    servicer, address = fake_coordinator
    _fleet(servicer)
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=size) as pilot:
        await _wait_places(app, pilot, 8)
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        labels = [str(col.label) for col in table.ordered_columns]
        # Protected columns are never dropped, at any width.
        for required in ("M", "Name", "S", "Capabilities"):
            assert required in labels, (size, labels)
        if size[0] >= 160:
            # Comfortable width: every column fits, Tags included.
            assert "Tags" in labels
            assert "Comment" in labels
            assert "Changed" in labels
            assert "User" in labels
        # 80 columns is the default terminal: Tags survives there, cut if
        # need be, because Comment is what width pressure takes first.
        assert "Tags" in labels, (size, labels)


@pytest.mark.parametrize("size", [(60, 24), (80, 24), (120, 24), (160, 24)])
async def test_tag_pairs_shared_by_every_place_are_cut_first(
    fake_coordinator: tuple[FakeCoordinator, str], size: tuple[int, int]
) -> None:
    """Every bench in this fleet carries site=lab1 and rack=a; only owner
    tells one from another. Width pressure takes the two shared pairs and
    leaves owner, on every row at once."""
    servicer, address = fake_coordinator
    _fleet(servicer)
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=size) as pilot:
        await _wait_places(app, pilot, 8)
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        cells = [str(table.get_cell(f"bench-{i:02d}", "tags")) for i in range(8)]
        # owner is what separates the benches, so it survives at every width
        # and starts at the same column on every row.
        assert all(cell.startswith(f"owner=user{i}") for i, cell in enumerate(cells))
        if size[0] == 60:
            # No room for three pairs: the two shared ones go, and they go
            # from every row at once, never from some of them.
            assert not any("site=" in cell or "rack=" in cell for cell in cells)
        if size[0] >= 160:
            assert all("rack=a" in cell and "site=lab1" in cell for cell in cells)


async def test_table_columns_restored_after_widening(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    _fleet(servicer)
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(60, 24)) as pilot:
        await _wait_places(app, pilot, 8)
        await pilot.pause()
        table = app.screen.query_one(DeviceTable)
        narrow_labels = [str(col.label) for col in table.ordered_columns]
        assert "Comment" not in narrow_labels

        await pilot.resize_terminal(200, 24)
        await pilot.pause()
        wide_labels = [str(col.label) for col in table.ordered_columns]
        assert "Tags" in wide_labels
        assert "Comment" in wide_labels
        assert "Changed" in wide_labels


async def test_terminal_too_small_notice_shown_and_hidden(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(50, 10)) as pilot:
        await pilot.pause()
        notice = app.screen.query_one("#too-small-notice", Static)
        main_row = app.screen.query_one("#main-row")
        assert "hidden" not in notice.classes
        assert "hidden" in main_row.classes
        text = str(notice.render())
        assert f"need {MIN_WIDTH}x{MIN_HEIGHT}" in text
        assert "have 50x10" in text

        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert "hidden" in notice.classes
        assert "hidden" not in main_row.classes


async def test_terminal_too_small_notice_updates_live(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(50, 10)) as pilot:
        await pilot.pause()
        notice = app.screen.query_one("#too-small-notice", Static)
        assert "have 50x10" in str(notice.render())

        await pilot.resize_terminal(55, 11)
        await pilot.pause()
        assert "hidden" not in notice.classes
        assert "have 55x11" in str(notice.render())
