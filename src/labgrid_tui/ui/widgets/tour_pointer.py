"""The tour's one pointer: a solid arrow block next to what a step is about.

It lives on its own ``tour`` layer, absolutely positioned by screen
coordinates, so it reads as chrome laid over the app rather than as part
of whatever it points at, and it looks the same on every screen. A modal
screen is a separate compositor, so each screen that shows guidance mounts
its own instance.

Placement is pure geometry (:func:`choose_placement`): pick the first side
that leaves the pointer fully on screen with a one-cell gap, then clamp.
Nothing here reads app state, so the choice is unit-testable without an app.
"""

import contextlib
from typing import Any, Literal

from textual.css.query import NoMatches
from textual.geometry import Offset, Region, Size
from textual.screen import Screen
from textual.widgets import Static

# Which side of the target the pointer sits on; the arrow points back at
# the target from there.
Side = Literal["right", "left", "below", "above"]

# Must match the width/height in DEFAULT_CSS: placement is computed before
# the widget has been laid out, so it cannot ask for its own size.
POINTER_SIZE = Size(7, 3)

# Cells left blank between the pointer and its target, so the pointer never
# covers the thing it is pointing at.
GAP = 1

_ARROWS: dict[Side, str] = {
    "right": "◀━━",
    "left": "━━▶",
    "below": "▲\n┃\n┃",
    "above": "┃\n┃\n▼",
}


def _centered(start: int, extent: int, size: int) -> int:
    return start + (extent - size) // 2


def _clamped(offset: Offset, pointer: Size, screen: Size) -> Offset:
    return Offset(
        max(0, min(offset.x, screen.width - pointer.width)),
        max(0, min(offset.y, screen.height - pointer.height)),
    )


def choose_placement(target: Region, pointer: Size, screen: Size) -> tuple[Offset, Side]:
    """Where to put a *pointer*-sized block so it points at *target*.

    Sides are tried right, left, below, above; the first one that keeps the
    pointer fully on *screen* along its own axis wins, which is what
    guarantees the gap and the non-overlap. The other axis is centered on the
    target and clamped, which cannot reintroduce an overlap because the two
    are already separated along the chosen axis.
    """
    across = _centered(target.y, target.height, pointer.height)
    down = _centered(target.x, target.width, pointer.width)
    candidates: tuple[tuple[Side, Offset], ...] = (
        ("right", Offset(target.right + GAP, across)),
        ("left", Offset(target.x - GAP - pointer.width, across)),
        ("below", Offset(down, target.bottom + GAP)),
        ("above", Offset(down, target.y - GAP - pointer.height)),
    )
    for side, offset in candidates:
        if side in ("right", "left"):
            fits = offset.x >= 0 and offset.x + pointer.width <= screen.width
        else:
            fits = offset.y >= 0 and offset.y + pointer.height <= screen.height
        if fits:
            return _clamped(offset, pointer, screen), side
    # Nothing fits (a target as large as the screen): stay in bounds and
    # accept the overlap rather than disappear.
    side, offset = candidates[0]
    return _clamped(offset, pointer, screen), side


class TourPointer(Static):
    """Solid accent block with a big arrow, pointing at one target region."""

    DEFAULT_CSS = """
    TourPointer {
        position: absolute;
        layer: tour;
        width: 7;
        height: 3;
        padding: 0 1;
        background: $accent;
        color: $text;
        text-style: bold;
        content-align: center middle;
        display: none;
    }
    """

    can_focus = False

    def __init__(self) -> None:
        super().__init__("", id="tour-pointer")
        # Which side of its target the pointer is currently on, for tests.
        self.side: Side | None = None

    def point_at(self, region: Region | None, screen_size: Size) -> None:
        """Move next to *region*, or hide when there is nothing to point at."""
        if region is None or not region.area:
            self.side = None
            self.display = False
            return
        offset, side = choose_placement(region, POINTER_SIZE, screen_size)
        self.side = side
        self.update(_ARROWS[side])
        self.styles.offset = (offset.x, offset.y)
        self.display = True


def update_pointer(screen: Screen[Any], region: Region | None) -> None:
    """Point *screen*'s pointer at *region*; a no-op if it has none mounted."""
    with contextlib.suppress(NoMatches):
        screen.query_one(TourPointer).point_at(region, screen.size)
