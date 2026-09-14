"""One-line step panel for `labgrid-tui tour`, docked above the footer."""

from textual.widgets import Static

from labgrid_tui.tour.steps import TourController


class TourPanel(Static):
    DEFAULT_CSS = """
    TourPanel {
        dock: bottom;
        height: 1;
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
        self.current_text = text
        hint = "" if text.startswith("Done.") else "  ([b]n[/] skip)"
        self.update(f"{text}{hint}")
