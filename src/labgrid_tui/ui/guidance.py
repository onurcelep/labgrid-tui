"""Guidance a modal screen can show for the built-in tour.

The dashboard hands a ``TourGuidance`` to the screen it pushes; the screen
renders the text as a plain line in its own hint slot, directly beside the
widget the step is about. Outside the tour every screen receives ``None``
and shows nothing, so this stays a sideband rather than tour state in the
screens.

The dashboard itself spotlights instead: the one top-level widget the step
is about keeps full colour while every other one is dimmed by ``tour-dim``.
Dimming blends a widget toward the background rather than drawing over it,
so no cell of the app is ever hidden by tour chrome.
"""

from dataclasses import dataclass

# Dashboard-side spotlight targets, in the dashboard's own terms. The
# tour's step table (labgrid_tui.tour.steps) names one of these per step;
# the dashboard resolves it to a widget and to a region for the step card.
POINT_TABLE = "table"
POINT_LOG = "log"
POINT_STATUS = "status"

# Set on every top-level widget of the dashboard except the one the
# current step is about.
DIM_CLASS = "tour-dim"

GUIDANCE_CSS = """
.tour-guidance {
    height: auto;
    margin-top: 1;
    text-align: center;
    padding: 0 1;
    color: $text-muted;
}
"""

SPOTLIGHT_CSS = """
.tour-dim { opacity: 45%; }
"""


@dataclass(frozen=True)
class TourGuidance:
    text: str
