"""The tour's step card: what it says, and the geometry that anchors it.

The card floats on the screen's ``tour`` layer, absolutely positioned by
screen coordinates, so it reads as chrome over the app and never changes
the layout underneath it. It carries no tour state of its own: the caller
pushes the title and the text, which keeps this widget free of any import
from the tour package.

Placement is pure geometry (:func:`choose_card_placement` on the dashboard,
:func:`choose_modal_placement` beside a dialog). Neither reads app state, so
both choices are unit-testable without an app.
"""

import contextlib
from typing import Any, Literal

from textual.css.query import NoMatches
from textual.geometry import Offset, Region, Size
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import Static

from labgrid_tui.ui.guidance import TourGuidance

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


def choose_modal_placement(dialog: Region, card: Size, screen: Size) -> Offset:
    """Where to put the card on a screen whose *dialog* box owns the middle.

    Beside the dialog when a side has room, tried below, above, right. When
    the dialog fills the terminal there is no outside left, and the card
    goes into its bottom-right corner: the tab strip, the list rows and the
    title a step points at all sit at the top of a dialog, so the corner is
    the furthest the card can be from whatever the step is about.
    """
    for offset in (
        Offset(dialog.x, dialog.bottom + CARD_GAP),
        Offset(dialog.x, dialog.y - CARD_GAP - card.height),
        Offset(dialog.right + CARD_GAP, dialog.y),
    ):
        inside = (
            offset.x >= 0
            and offset.y >= 0
            and offset.x + card.width <= screen.width
            and offset.y + card.height <= screen.height
        )
        if inside:
            return offset
    return _clamped(Offset(dialog.right - card.width, dialog.bottom - card.height), card, screen)


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


def refresh_modal_card(screen: Screen[Any], guidance: TourGuidance | None, dialog: str) -> None:
    """Fill the step card on a modal *screen*, then queue its placement.

    Outside the tour every screen is handed ``None`` and hides its card, so
    a modal can mount one unconditionally and stay free of tour state.
    """
    with contextlib.suppress(NoMatches):
        card = screen.query_one(TourCard)
        card.display = guidance is not None
        if guidance is not None:
            card.show(guidance.title, guidance.text)
    screen.call_after_refresh(place_modal_card, screen, dialog)


def place_modal_card(screen: Screen[Any], dialog: str) -> None:
    """Put the step card beside the *dialog* box, or in its corner."""
    try:
        card = screen.query_one(TourCard)
        region = screen.query_one(dialog, Widget).region
    except NoMatches:
        return
    if not card.display or not region.area:
        return
    width = card_width(screen.size)
    if card.applied_width != width:
        card.applied_width = width
        card.styles.width = width
        # The card's height follows from wrapping its text at the new width,
        # which only the next layout pass knows.
        screen.call_after_refresh(place_modal_card, screen, dialog)
    size = card.outer_size
    if not size.area:
        return
    offset = choose_modal_placement(region, size, screen.size)
    card.styles.offset = (offset.x, offset.y)
