"""Guidance a modal screen can show for the built-in tour.

The dashboard hands a ``TourGuidance`` to the screen it pushes; the screen
renders the text as a plain line in its own hint slot and moves its
``TourPointer`` next to the one thing the step is about. Outside the tour
every screen receives ``None`` and shows nothing, so this stays a sideband
rather than tour state in the screens.
"""

from dataclasses import dataclass

# Dashboard-side pointer targets, in the dashboard's own terms. The tour's
# step table (labgrid_tui.tour.steps) names one of these per step; the
# dashboard resolves it to a screen region.
POINT_TABLE = "table"
POINT_LOG = "log"
POINT_STATUS = "status"

GUIDANCE_CSS = """
.tour-guidance {
    height: auto;
    margin-top: 1;
    text-align: center;
    padding: 0 1;
    color: $text-muted;
}
"""


@dataclass(frozen=True)
class TourGuidance:
    text: str
    # What to point at, in the pushed screen's own terms: a tab title
    # ("*" for whichever tab is active) in the command overlay, an entry
    # name in the coordinator selector, "title" in the detail overlay.
    target: str | None = None
