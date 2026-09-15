"""Guidance a modal screen can show for the built-in tour.

The dashboard hands a ``TourGuidance`` to the screen it pushes; the screen
renders the text in its own hint area and outlines the widget named by
``focus``. Outside the tour every screen receives ``None`` and shows
nothing, so this stays a sideband rather than tour state in the screens.
"""

from dataclasses import dataclass

# CSS class that outlines the widget the tour asks the user to look at.
# ``outline`` draws inside the widget, so toggling it never moves layout.
FOCUS_CLASS = "-tour-focus"

GUIDANCE_CSS = """
.tour-guidance {
    height: auto;
    margin-top: 1;
    text-align: center;
    padding: 0 1;
    background: $warning;
    color: $text;
}
.-tour-focus { outline: thick $warning; }
"""


@dataclass(frozen=True)
class TourGuidance:
    text: str
    # Widget id (without "#") to outline, if it exists on that screen.
    focus: str | None = None
