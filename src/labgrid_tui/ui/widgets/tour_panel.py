"""One-line step panel for `labgrid-tui tour`, shown above the footer."""

from textual.widgets import Static

from labgrid_tui.tour.steps import TourController


class TourPanel(Static):
    DEFAULT_CSS = """
    /* In the vertical flow, composed right before the docked Footer, so it
       takes the row above the key hints instead of painting over them. */
    TourPanel {
        height: auto;
        max-height: 3;
        padding: 0 1;
        background: $accent;
        color: $text;
    }
    """

    def __init__(self, controller: TourController) -> None:
        super().__init__("", id="tour-panel")
        self._controller = controller
        # Plain-text mirror of what's on screen, for tests: Static wraps its
        # content in a Rich/Textual Visual internally with no simple public
        # accessor back to the original string.
        self.current_text = ""

    def on_mount(self) -> None:
        self._controller.on_change = self._set_text
        self._set_text(self._controller.label())

    def _set_text(self, text: str) -> None:
        # No skip hint here: the footer shows "n Skip step" (the binding is
        # show=True) and the welcome toast says it once.
        self.current_text = text
        self.update(text)
