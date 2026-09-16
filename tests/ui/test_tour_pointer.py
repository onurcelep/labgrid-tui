"""The tour pointer: pure placement geometry, then what it paints in a real app.

The geometry tests need no app at all, which is the point of keeping
``choose_placement`` a plain function. The app tests assert against the
compositor, so they fail if the pointer is laid out but never painted.
"""

import asyncio
from collections.abc import Callable

import grpc.aio
import pytest
from textual.geometry import Region, Size
from textual.widgets import TabbedContent

from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.tour.app import TourApp
from labgrid_tui.tour.welcome import WelcomeScreen
from labgrid_tui.ui.screens.command_overlay import CommandOverlay, _slug
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.tour_card import TourCard
from labgrid_tui.ui.widgets.tour_pointer import POINTER_SIZE, TourPointer, choose_placement
from tests.ui.pointer_asserts import assert_points_at, painted_pointer

SCREEN = Size(80, 24)


def _place(target: Region, screen: Size = SCREEN) -> tuple[Region, str]:
    offset, side = choose_placement(target, POINTER_SIZE, screen)
    region = Region(offset.x, offset.y, POINTER_SIZE.width, POINTER_SIZE.height)
    return region, side


def _in_bounds(region: Region, screen: Size) -> bool:
    return (
        region.x >= 0
        and region.y >= 0
        and region.right <= screen.width
        and region.bottom <= screen.height
    )


def test_right_is_preferred_and_leaves_one_clear_cell() -> None:
    target = Region(10, 10, 20, 1)
    region, side = _place(target)
    assert side == "right"
    assert region.x == target.right + 1
    # Vertically centered on a one-row target.
    assert region.y <= target.y < region.bottom


def test_falls_back_to_the_left_when_the_right_is_off_screen() -> None:
    target = Region(20, 5, 58, 1)  # right edge at 78, no room for 7 + gap
    region, side = _place(target)
    assert side == "left"
    assert region.right == target.x - 1


def test_falls_back_below_when_neither_side_fits() -> None:
    target = Region(0, 5, 80, 1)  # full width: no room either side
    region, side = _place(target)
    assert side == "below"
    assert region.y == target.bottom + 1


def test_falls_back_above_when_below_would_leave_the_screen() -> None:
    target = Region(0, 20, 80, 4)  # full width, flush with the bottom
    region, side = _place(target)
    assert side == "above"
    assert region.bottom == target.y - 1


@pytest.mark.parametrize(
    "target",
    [
        Region(0, 0, 1, 1),
        Region(79, 0, 1, 1),
        Region(0, 23, 1, 1),
        Region(79, 23, 1, 1),
        Region(0, 0, 80, 24),
        Region(0, 0, 80, 1),
        Region(0, 23, 80, 1),
    ],
)
def test_never_leaves_the_screen(target: Region) -> None:
    region, _side = _place(target)
    assert _in_bounds(region, SCREEN)


@pytest.mark.parametrize("screen", [Size(40, 12), Size(60, 20), Size(200, 50)])
def test_stays_in_bounds_on_any_screen(screen: Size) -> None:
    for target in (
        Region(0, 0, 1, 1),
        Region(screen.width - 1, screen.height - 1, 1, 1),
        Region(0, 0, screen.width, screen.height),
        Region(screen.width // 2, screen.height // 2, 4, 2),
    ):
        region, _side = _place(target, screen)
        assert _in_bounds(region, screen), (target, region, screen)


def test_the_arrow_points_back_at_the_target() -> None:
    pointer = TourPointer()
    pointer.point_at(Region(10, 10, 4, 1), SCREEN)
    assert pointer.side == "right"
    assert "◀" in str(pointer.render())
    pointer.point_at(Region(0, 5, 80, 1), SCREEN)
    assert pointer.side == "below"
    assert "▲" in str(pointer.render())
    pointer.point_at(None, SCREEN)
    assert pointer.side is None
    assert pointer.display is False


# ----------------------------------------------------------------------
# In a running tour
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_grpc_channel(monkeypatch: pytest.MonkeyPatch) -> None:
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


async def _started(app: TourApp, pilot: object) -> None:
    assert await _wait_until(pilot, lambda: app.store.conn is ConnState.LIVE)
    assert isinstance(app.screen, WelcomeScreen)
    await pilot.press("enter")  # type: ignore[attr-defined]
    assert await _wait_until(pilot, lambda: bool(app.screen_stack[0].query(TourCard)))
    await pilot.pause()  # type: ignore[attr-defined]


@pytest.mark.parametrize("size", [(80, 24), (60, 20), (200, 50)])
async def test_pointer_tracks_the_table_cursor(size: tuple[int, int]) -> None:
    app = TourApp()
    async with app.run_test(size=size) as pilot:
        await _started(app, pilot)
        dashboard = app.screen_stack[0]
        table = dashboard.query_one(DeviceTable)
        first = table.cursor_row_region()
        assert first is not None
        assert_points_at(dashboard, first, size)

        await pilot.press("j")
        await pilot.pause()
        await pilot.pause()
        second = table.cursor_row_region()
        assert second is not None and second != first
        assert_points_at(dashboard, second, size)


@pytest.mark.parametrize("size", [(80, 24), (60, 20), (200, 50)])
async def test_pointer_follows_the_active_tab_in_the_command_overlay(
    size: tuple[int, int],
) -> None:
    app = TourApp()
    async with app.run_test(size=size) as pilot:
        await _started(app, pilot)
        await pilot.press("j")
        await pilot.press("r")
        await pilot.pause()
        await pilot.press("c")
        await pilot.pause()
        await pilot.pause()
        overlay = app.screen
        assert isinstance(overlay, CommandOverlay)
        tabs = overlay.query_one(TabbedContent)
        categories = list(overlay._by_category)
        assert_points_at(overlay, tabs.get_tab(f"tab-{_slug(categories[0])}").region, size)

        await pilot.press("right")
        await pilot.pause()
        await pilot.pause()
        assert_points_at(overlay, tabs.get_tab(f"tab-{_slug(categories[1])}").region, size)


async def test_pointer_hides_when_its_target_is_gone() -> None:
    app = TourApp()
    async with app.run_test(size=(80, 24)) as pilot:
        await _started(app, pilot)
        dashboard = app.screen_stack[0]
        # Step 5 points at the activity log; hiding it leaves nothing to
        # point at, exactly as the narrow layout would.
        for _ in range(4):
            await pilot.press("n")
            await pilot.pause()
        await pilot.pause()
        assert painted_pointer(dashboard) is not None
        await pilot.press("a")  # toggles the activity log off
        await pilot.pause()
        await pilot.pause()
        assert dashboard.query_one(TourPointer).display is False
