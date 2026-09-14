"""Confirmation modal for deleting a named coordinator.

Takes the name to confirm via constructor argument only; the caller
decides what deleting it means.
"""

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class CoordinatorDeleteConfirm(ModalScreen[bool]):
    """Dismisses with ``True`` to confirm deletion, ``False`` to cancel."""

    DEFAULT_CSS = """
    CoordinatorDeleteConfirm { align: center middle; }
    #coord-delete-modal {
        width: 50;
        height: auto;
        background: $surface;
        border: thick $error;
        padding: 1 2;
    }
    #coord-delete-title {
        text-style: bold;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #coord-delete-message {
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #coord-delete-modal > Horizontal {
        width: 100%;
        height: auto;
        align: center middle;
    }
    #coord-delete-modal > Horizontal > Button { margin: 0 1; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("n", "cancel", "Cancel", show=False),
        Binding("y", "confirm", "Delete", show=False),
        Binding("left", "app.focus_previous", "Previous", show=False),
        Binding("right", "app.focus_next", "Next", show=False),
    ]

    def __init__(self, name: str) -> None:
        super().__init__()
        self._name = name

    def compose(self) -> ComposeResult:
        with Vertical(id="coord-delete-modal"):
            yield Static("Delete Coordinator", id="coord-delete-title")
            yield Static(
                f'Delete coordinator "{self._name}"? This cannot be undone.',
                id="coord-delete-message",
            )
            with Horizontal():
                yield Button("Cancel", id="coord-delete-cancel", variant="default")
                yield Button("Delete", id="coord-delete-confirm", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(event.button.id == "coord-delete-confirm")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)
