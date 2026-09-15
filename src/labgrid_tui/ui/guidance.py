"""Guidance a modal screen can show for the built-in tour.

The dashboard hands a ``TourGuidance`` to the screen it pushes; the screen
renders the text as a plain line in its own hint slot and puts the tour's
pointer next to the one thing the step is about. Outside the tour every
screen receives ``None`` and shows nothing, so this stays a sideband
rather than tour state in the screens.
"""

from dataclasses import dataclass

# The tour's pointer: a single arrow placed next to whatever a step is
# about (a tab title, a list row, a table row, a title line). One glyph,
# no borders, so it never moves layout and never hides anything.
MARKER = "\u2192"

GUIDANCE_CSS = """
.tour-guidance {
    height: auto;
    margin-top: 1;
    text-align: center;
    padding: 0 1;
    color: $text-muted;
}
"""


def mark(text: str, pointed: bool) -> str:
    """*text* with the pointer in front when *pointed*, else padded so the
    rows it sits among keep their alignment."""
    return f"{MARKER} {text}" if pointed else f"  {text}"


@dataclass(frozen=True)
class TourGuidance:
    text: str
    # What to point at, in the pushed screen's own terms: a tab title
    # ("*" for whichever tab is active) in the command overlay, an entry
    # name in the coordinator selector, "title" in the detail overlay.
    target: str | None = None
