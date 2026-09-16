"""The tour's step card: what it says, and the geometry that anchors it.

The card floats on the screen's ``tour`` layer, absolutely positioned by
screen coordinates, so it reads as chrome over the app and never changes
the layout underneath it. It carries no tour state of its own: the caller
pushes the title and the text, which keeps this widget free of any import
from the tour package.

Placement is pure geometry (:func:`choose_card_placement`): the card sits
against one edge of the region the step is about, preferring below, and is
clamped into the screen. Nothing here reads app state, so the choice is
unit-testable without an app.
"""

from typing import Literal

from textual.geometry import Offset, Region, Size
from textual.widgets import Static

# Which edge of the target the card sits against.
Side = Literal["below", "above", "right", "left"]

CARD_WIDTH = 44
# Cells left between the card and its target. Zero reads as attached to
# the target; the card's own border still separates the two visually.
CARD_GAP = 0


def card_width(screen: Size) -> int:
    """The card's width on a *screen* this wide, narrow terminals included."""
    return min(CARD_WIDTH, max(1, screen.width - 2))


def _clamped(offset: Offset, card: Size, screen: Size) -> Offset:
    return Offset(
        max(0, min(offset.x, screen.width - card.width)),
        max(0, min(offset.y, screen.height - card.height)),
    )


def centered_offset(card: Size, screen: Size) -> Offset:
    """Where to put the card when the step is about nothing in particular."""
    return Offset(
        max(0, (screen.width - card.width) // 2),
        max(0, (screen.height - card.height) // 2),
    )


def choose_card_placement(target: Region, card: Size, screen: Size) -> tuple[Offset, Side]:
    """Where to put a *card*-sized box so it is anchored to *target*.

    Edges are tried below, above, right, left; the first one that keeps the
    card fully on *screen* along its own axis wins, which is what guarantees
    the card never covers the target. The other axis is aligned with the
    target's near edge and clamped, which cannot reintroduce an overlap
    because the two are already separated along the chosen axis.
    """
    candidates: tuple[tuple[Side, Offset], ...] = (
        ("below", Offset(target.x, target.bottom + CARD_GAP)),
        ("above", Offset(target.x, target.y - CARD_GAP - card.height)),
        ("right", Offset(target.right + CARD_GAP, target.y)),
        ("left", Offset(target.x - CARD_GAP - card.width, target.y)),
    )
    for side, offset in candidates:
        if side in ("below", "above"):
            fits = offset.y >= 0 and offset.y + card.height <= screen.height
        else:
            fits = offset.x >= 0 and offset.x + card.width <= screen.width
        if fits:
            return _clamped(offset, card, screen), side
    # Nothing fits (a target as large as the screen): stay in bounds and
    # accept the overlap rather than disappear.
    side, offset = candidates[0]
    return _clamped(offset, card, screen), side


class TourCard(Static):
    """One step's title and text, floating beside what the step is about."""

    DEFAULT_CSS = """
    TourCard {
        position: absolute;
        layer: tour;
        width: 44;
        height: auto;
        border: round $accent;
        border-title-color: $text;
        border-title-style: bold;
        background: $panel;
        padding: 0 1;
    }
    """

    can_focus = False

    def __init__(self) -> None:
        super().__init__("", id="tour-card")
        # Plain-text mirror of what is on screen, for tests: Static keeps
        # its content as a Visual with no simple accessor to the string.
        self.current_text = ""
        # The width last written to styles: reading it back off Styles
        # would mean unpacking a Scalar to compare it with a cell count.
        self.applied_width = 0

    def show(self, title: str, text: str) -> None:
        self.current_text = text
        self.border_title = title
        self.update(text)
