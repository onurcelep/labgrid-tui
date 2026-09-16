"""Guidance a modal screen can show for the built-in tour.

The dashboard hands a ``TourGuidance`` to the screen it pushes, and that
screen shows the same step card the dashboard does, placed beside its
dialog box. Outside the tour every screen receives ``None`` and shows
nothing, so this stays a sideband rather than tour state in the screens.

The dashboard itself spotlights: the one top-level widget the step is
about keeps full colour while every other one is dimmed by ``tour-dim``.
Dimming blends a widget toward the background rather than drawing over
it, so no cell of the app is ever hidden by tour chrome.
"""

from dataclasses import dataclass

# Dashboard-side spotlight targets, in the dashboard's own terms. The
# tour's step table (labgrid_tui.tour.steps) names one of these per step;
# the dashboard resolves it to the widget that stays bright.
POINT_TABLE = "table"
POINT_LOG = "log"
POINT_STATUS = "status"

# Set on every top-level widget of the dashboard except the one the
# current step is about.
DIM_CLASS = "tour-dim"

SPOTLIGHT_CSS = """
.tour-dim { opacity: 45%; }
"""


@dataclass(frozen=True)
class TourGuidance:
    title: str
    text: str
