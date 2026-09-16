"""Modal listing named coordinators: switch, create, edit, delete.

List + single-key actions instead of list entries; no detail panel or
auth/backend fields, since a labgrid-tui coordinator has neither. Takes
its data via constructor arguments only, no app-specific imports, so
it stays part of the reusable surface documented in the README's
"Building on labgrid-tui" section.
"""

from dataclasses import dataclass

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, ListItem, ListView, Static

from labgrid_tui.coordinators import CoordinatorEntry
from labgrid_tui.ui.guidance import TourGuidance
from labgrid_tui.ui.widgets.tour_card import TourCard, place_modal_card, refresh_modal_card


@dataclass(frozen=True)
class SwitchResult:
    name: str


@dataclass(frozen=True)
class CreateResult:
    pass


@dataclass(frozen=True)
class EditResult:
    name: str


@dataclass(frozen=True)
class DeleteResult:
    name: str


CoordinatorSelectorResult = SwitchResult | CreateResult | EditResult | DeleteResult | None


DIALOG = "#coord-modal"


class CoordinatorSelector(ModalScreen[CoordinatorSelectorResult]):
    DEFAULT_CSS = """
    CoordinatorSelector { align: center middle; layers: base tour; }
    #coord-modal {
        width: 90%;
        max-width: 90;
        min-width: 40;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #coord-title {
        text-style: bold;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #coord-list { height: auto; max-height: 20; }
    #coord-list > ListItem { height: 1; padding: 0 1; }
    #coord-list > ListItem:hover { background: $accent 30%; }
    #coord-hint {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
        width: 100%;
    }

    CoordinatorSelector.-narrow #coord-modal { width: 100%; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_none", "Cancel", show=True),
        Binding("q", "dismiss_none", "Cancel", show=False),
        # ListView already binds up/down/enter itself (it holds focus); j/k
        # are added here only as vim aliases: they reach this screen-level
        # action because the focused ListView has no binding for them.
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("n", "new_coordinator", "New", show=True),
        Binding("e", "edit_coordinator", "Edit", show=True),
        Binding("x", "delete_coordinator", "Delete", show=True),
        # Mirrors ctrl+p, which already reaches this modal as an App-level
        # priority binding; "app." routes to App.action_command_palette
        # since this screen has none of its own.
        Binding("colon", "app.command_palette", "Palette", show=False),
    ]

    def __init__(
        self,
        entries: list[CoordinatorEntry],
        current: str | None,
        guidance: TourGuidance | None = None,
    ) -> None:
        super().__init__()
        self._guidance = guidance
        self._entries = entries
        self._current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="coord-modal"):
            yield Static("Coordinators", id="coord-title")
            items: list[ListItem] = []
            initial_index = 0
            for idx, entry in enumerate(self._entries):
                items.append(ListItem(Label(self._row_text(entry))))
                if entry.name == self._current:
                    initial_index = idx
            yield ListView(*items, id="coord-list", initial_index=initial_index)
            yield Static(
                "enter=switch | n=new | e=edit | x=delete | esc/q=close",
                id="coord-hint",
            )
        # A screen child, not part of the dialog box: the card is placed
        # beside it by screen coordinates on its own layer.
        card = TourCard()
        card.display = False
        yield card

    def on_mount(self) -> None:
        self.query_one("#coord-list", ListView).focus()
        self.update_guidance(self._guidance)

    def on_resize(self, _event: events.Resize) -> None:
        place_modal_card(self, DIALOG)

    def _selected_name(self) -> str | None:
        lv = self.query_one("#coord-list", ListView)
        idx = lv.index
        if idx is None or not (0 <= idx < len(self._entries)):
            return None
        return self._entries[idx].name

    def action_cursor_down(self) -> None:
        self.query_one("#coord-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#coord-list", ListView).action_cursor_up()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        name = self._selected_name()
        if name is not None:
            self.dismiss(SwitchResult(name))

    def action_new_coordinator(self) -> None:
        self.dismiss(CreateResult())

    def action_edit_coordinator(self) -> None:
        name = self._selected_name()
        if name is not None:
            self.dismiss(EditResult(name))

    def action_delete_coordinator(self) -> None:
        name = self._selected_name()
        if name is not None:
            self.dismiss(DeleteResult(name))

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def update_guidance(self, guidance: TourGuidance | None) -> None:
        """Tour sideband: show this step's card beside the dialog box."""
        self._guidance = guidance
        refresh_modal_card(self, guidance, DIALOG)

    def _row_text(self, entry: CoordinatorEntry) -> str:
        current = " *" if entry.name == self._current else ""
        return f"{entry.name}{current}  {entry.address}"
