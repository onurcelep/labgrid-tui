"""Single source of truth for responsive breakpoints and layout helpers.

``HORIZONTAL_BREAKPOINTS`` / ``VERTICAL_BREAKPOINTS`` are consumed by
``Screen._on_resize`` (see ``textual/screen.py``): Textual adds the matching
class to the active screen automatically on every resize, so every screen in
this app gets ``-narrow``/``-normal``/``-wide`` and ``-short`` for free by
inheriting them from ``LabgridTuiApp``: nothing here needs to call into
Textual's resize machinery directly.
"""

from textual.geometry import Size

# Width tiers, in cells: -narrow (<80), -normal (80-119), -wide (>=120).
HORIZONTAL_BREAKPOINTS: list[tuple[int, str]] = [
    (0, "-narrow"),
    (80, "-normal"),
    (120, "-wide"),
]

# Height tiers, in rows: -short (<20). The second entry has no CSS of its
# own; it exists only so Textual clears "-short" once height reaches 20
# (Screen._on_resize picks exactly one class per breakpoint list).
VERTICAL_BREAKPOINTS: list[tuple[int, str]] = [
    (0, "-short"),
    (20, "-regular-height"),
]

# Below this size the dashboard shows a "terminal too small" notice
# instead of its normal content: smaller than this and the fixed chrome
# (status bar, footer, borders) no longer leaves room for anything usable.
MIN_WIDTH = 60
MIN_HEIGHT = 12


def too_small(size: Size) -> bool:
    """Whether ``size`` is below the minimum usable terminal size."""
    return size.width < MIN_WIDTH or size.height < MIN_HEIGHT


def is_narrow(size: Size) -> bool:
    """Whether ``size`` falls in the ``-narrow`` width tier.

    Screens should prefer this over ``has_class("-narrow")`` when the
    result feeds a layout decision made during ``on_mount``/``compose``:
    a screen's own ``on_resize``/``on_mount`` handler runs before
    ``Screen._on_resize`` (the base-class handler that sets breakpoint
    classes), because Textual dispatches handlers most-derived-class
    first. On a freshly pushed screen the breakpoint classes are not yet
    applied when the screen's own handler runs, so ``has_class`` reads
    stale (always False) on cold open.
    """
    return size.width < HORIZONTAL_BREAKPOINTS[1][0]


def is_short(size: Size) -> bool:
    """Whether ``size`` falls in the ``-short`` height tier.

    See :func:`is_narrow` for why this reads live geometry rather than
    the ``-short`` CSS class.
    """
    return size.height < VERTICAL_BREAKPOINTS[1][0]


def middle_ellipsis(text: str, width: int) -> str:
    """Shorten ``text`` to ``width`` cells, eliding the middle.

    Keeps both the head and the tail, where the distinguishing part of a
    place name or address usually lives (shared prefixes, host:port
    suffixes): unlike trailing truncation, which would collapse a whole
    fleet of same-prefixed place names to the same visible string.
    """
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width == 1:
        return "…"
    budget = width - 1  # one cell reserved for the ellipsis itself
    head = (budget + 1) // 2
    tail = budget - head
    return f"{text[:head]}…{text[-tail:]}" if tail else f"{text[:head]}…"
