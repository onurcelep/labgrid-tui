"""Shared assertions about where the tour pointer ended up.

Not a test module: both the pointer's own tests and the tour walkthrough
tests assert the same paint-level property, so it lives in one place.
"""

from textual.geometry import Region
from textual.screen import Screen

from labgrid_tui.ui.widgets.tour_pointer import TourPointer


def painted_pointer(screen: Screen[object]) -> Region | None:
    """The pointer's region as the compositor placed it, or None if it shows none.

    Read from the compositor rather than from the widget, so this reflects
    what is on the terminal and not merely what was asked for.
    """
    pointers = screen.query(TourPointer)
    if not pointers:
        return None
    pointer = pointers.first()
    placed = screen._compositor.visible_widgets.get(pointer)
    if placed is None or not pointer.display:
        return None
    return placed[0]


def gap_between(pointer: Region, target: Region) -> int | None:
    """Clear cells between the two regions, or None when they overlap."""
    if pointer.x >= target.right:
        return pointer.x - target.right
    if target.x >= pointer.right:
        return target.x - pointer.right
    if pointer.y >= target.bottom:
        return pointer.y - target.bottom
    if target.y >= pointer.bottom:
        return target.y - pointer.bottom
    return None


def assert_points_at(screen: Screen[object], target: Region, size: tuple[int, int]) -> None:
    """The pointer is painted one clear cell from *target* and fully on screen."""
    pointer = painted_pointer(screen)
    assert pointer is not None, "no pointer painted"
    assert gap_between(pointer, target) == 1, (pointer, target)
    assert pointer.x >= 0 and pointer.y >= 0
    assert pointer.right <= size[0] and pointer.bottom <= size[1]
