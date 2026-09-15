"""Opening card of `labgrid-tui tour`: what the app is, why, how the tour works."""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

WELCOME_LINES = (
    "labgrid-tui is a live view of a labgrid lab: every bench, who holds it, "
    "what it offers, and the exact labgrid-client line for each action, "
    "ready to copy.",
    "It only reads the coordinator: see the whole lab at once, learn the CLI "
    "by using it, change nothing by accident.",
    "This tour runs on fake data. Keys work as usual; a card names the next "
    "key and an arrow points at what it means. n skips, q quits. Enter starts.",
)


class WelcomeScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    WelcomeScreen { align: center middle; }
    #welcome-card {
        width: 72;
        max-width: 95%;
        height: auto;
        border: round $accent;
        border-title-color: $text;
        border-title-style: bold;
        background: $panel;
        padding: 1 2;
    }
    #welcome-card > Static { height: auto; margin-bottom: 1; }
    #welcome-card > Static:last-child { margin-bottom: 0; }
    """

    BINDINGS = [
        Binding("enter", "start", "Start", show=False),
        Binding("n", "start", "Start", show=False),
        Binding("q", "app.quit", "Quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="welcome-card"):
            for line in WELCOME_LINES:
                yield Static(line)

    def on_mount(self) -> None:
        self.query_one("#welcome-card", Vertical).border_title = "labgrid-tui tour"

    def action_start(self) -> None:
        self.dismiss(None)
