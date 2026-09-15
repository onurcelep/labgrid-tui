"""Floating step card for `labgrid-tui tour`.

Absolutely positioned on its own layer inside the fleet-table row, so it
floats over free table space without moving anything else and never takes
focus: every key keeps going to the app.
"""

from textual.widgets import Static

from labgrid_tui.tour.steps import TourController

CARD_WIDTH = 44
# Row below the six fake benches (header row + rows), plus one blank line.
CARD_TOP = 8


class TourCard(Static):
    DEFAULT_CSS = """
    TourCard {
        position: absolute;
        layer: tour;
        width: 44;
        height: auto;
        border: round $warning;
        border-title-color: $text;
        border-title-style: bold;
        background: $panel;
        padding: 0 1;
    }
    """

    def __init__(self, controller: TourController) -> None:
        super().__init__("", id="tour-card")
        self._controller = controller
        # Plain-text mirror of what is on screen, for tests: Static keeps
        # its content as a Visual with no simple accessor to the string.
        self.current_text = ""

    def on_mount(self) -> None:
        self.refresh_text()

    def refresh_text(self) -> None:
        self.current_text = self._controller.label()
        self.border_title = self._controller.title()
        self.update(self.current_text)
