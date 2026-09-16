"""Centered device-detail modal.

Info only: commands live in the CommandOverlay, reachable directly from
here with ``c``. The body live-refreshes on fleet events via the app's
duck-typed ``refresh_fleet`` walk; a place removed while open renders as
removed until dismissed.
"""

from datetime import UTC, datetime

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import Static

from labgrid_tui.model.capabilities import capability_of
from labgrid_tui.model.commands import CommandEntry, evaluate
from labgrid_tui.model.identity import current_id
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.clipboard import copy_via_osc52, copy_via_suspend
from labgrid_tui.ui.format import abbrev
from labgrid_tui.ui.guidance import TourGuidance
from labgrid_tui.ui.layout import is_narrow
from labgrid_tui.ui.store import FleetStore
from labgrid_tui.ui.widgets.tour_card import TourCard, place_modal_card, refresh_modal_card


def _format_timestamp(epoch: float) -> str:
    """Absolute UTC timestamp, ``%Y-%m-%d %H:%M:%S`` format."""
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


DIALOG = "#detail-modal"


class DetailOverlay(ModalScreen[None]):
    DEFAULT_CSS = """
    DetailOverlay { align: center middle; layers: base tour; }
    #detail-modal {
        width: 95%;
        max-width: 100;
        height: 90%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #detail-title {
        text-style: bold;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #detail-scroll { height: 1fr; }
    #detail-body { height: auto; }
    #detail-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        text-align: center;
        width: 100%;
    }

    DetailOverlay.-narrow #detail-modal { width: 100%; height: 100%; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_overlay", "Close"),
        Binding("d", "dismiss_overlay", "Close", show=False),
        Binding("enter,y", "copy_details", "Copy"),
        # Suspend-and-print fallback for terminals that drop OSC 52.
        Binding("shift+enter", "copy_via_select", "Copy via select", show=True),
        Binding("c", "commands", "Cmds"),
        # Screen-level so the body scrolls regardless of which widget holds
        # focus: App-level BINDINGS never reach a ModalScreen in Textual
        # 8.2.8, so these can't be inherited from the dashboard.
        Binding("j,down", "scroll_body_down", "Down", show=False),
        Binding("k,up", "scroll_body_up", "Up", show=False),
        Binding("pagedown,ctrl+d", "scroll_body_page_down", "Page down", show=False),
        Binding("pageup,ctrl+u", "scroll_body_page_up", "Page up", show=False),
        Binding("g,home", "scroll_body_home", "Top", show=False),
        Binding("G,end", "scroll_body_end", "Bottom", show=False),
        # Mirrors ctrl+p, which already reaches this modal as an App-level
        # priority binding; "app." routes the action to App.action_command_palette
        # since this screen has no action_command_palette of its own.
        Binding("colon", "app.command_palette", "Palette", show=False),
    ]

    def __init__(
        self, place_name: str, runner: ActionRunner, guidance: TourGuidance | None = None
    ) -> None:
        super().__init__()
        self._guidance = guidance
        self.place_name = place_name
        self._runner = runner
        self._plain_text = ""

    def compose(self) -> ComposeResult:
        # Plain Vertical, not VerticalScroll: a focusable outer box would
        # steal Textual's auto-focus from #detail-scroll (the only widget
        # that should ever hold focus here).
        with Vertical(id="detail-modal"):
            yield Static("", id="detail-title")
            with VerticalScroll(id="detail-scroll"):
                yield Static("", id="detail-body")
            yield Static(
                "enter/y = copy | shift+enter = copy via select | c = commands | esc = close",
                id="detail-hint",
            )
        # A screen child, not part of the dialog box: the card is placed
        # beside it by screen coordinates on its own layer.
        card = TourCard()
        card.display = False
        yield card

    def on_mount(self) -> None:
        self.refresh_fleet()
        self.update_guidance(self._guidance)
        self.set_focus(self.query_one("#detail-scroll", VerticalScroll))

    def on_resize(self, _event: events.Resize) -> None:
        # -narrow changes how resource lines are laid out (see
        # refresh_fleet); re-render existing content on resize rather than
        # waiting for the next fleet event to pick it up.
        self.refresh_fleet()
        place_modal_card(self, DIALOG)

    def _scroll_body(self) -> VerticalScroll:
        return self.query_one("#detail-scroll", VerticalScroll)

    def action_scroll_body_down(self) -> None:
        self._scroll_body().scroll_down(animate=False)

    def action_scroll_body_up(self) -> None:
        self._scroll_body().scroll_up(animate=False)

    def action_scroll_body_page_down(self) -> None:
        self._scroll_body().scroll_page_down(animate=False)

    def action_scroll_body_page_up(self) -> None:
        self._scroll_body().scroll_page_up(animate=False)

    def action_scroll_body_home(self) -> None:
        self._scroll_body().scroll_home(animate=False)

    def action_scroll_body_end(self) -> None:
        self._scroll_body().scroll_end(animate=False)

    def _reservation_text(self, store: FleetStore, token: str) -> str:
        for reservation in store.reservations:
            if reservation.token == token:
                marker = " (yours)" if reservation.owner == current_id() else ""
                return (
                    f"reservation {token} ({reservation.owner}, {reservation.state.name}){marker}"
                )
        return f"reservation {token}"

    def refresh_fleet(self) -> None:
        store = getattr(self.app, "store", None)
        if store is None:
            return
        try:
            title = self.query_one("#detail-title", Static)
        except NoMatches:
            return  # fleet event raced our own mount
        body = self.query_one("#detail-body", Static)

        place = store.places.get(self.place_name)
        if place is None:
            title.update(f"Device: {self.place_name} (removed)")
            body.update("this place no longer exists on the coordinator")
            self._plain_text = f"{self.place_name}: removed"
            return

        title.update(f"Device: {place.name}")
        text = Text()
        plain: list[str] = [f"Device: {place.name}"]

        def kv(key: str, value: str, value_style: str = "") -> None:
            text.append(f"  {key + ':':<14s}", style="dim")
            text.append(f"{value}\n", style=value_style)
            plain.append(f"{key + ':':<14s}{value}".rstrip())

        # Identity
        if place.acquired:
            state = "Acquired"
        elif place.reservation:
            state = "Reserved"
        else:
            state = "Free"
        kv("Name", place.name)
        kv("State", state)
        kv("Acquired by", place.acquired or "-")
        if place.allowed:
            kv("Shared with", ", ".join(place.allowed))
        if place.reservation:
            kv("Reservation", self._reservation_text(store, place.reservation))
        if place.created:
            kv("Created", _format_timestamp(place.created))
        if place.changed:
            kv("Changed", _format_timestamp(place.changed))
        text.append("\n")

        # Resources this place matches, as chips
        resources = store.resources_of(place)
        capability_extra = getattr(self.app, "capability_extra", None)
        online: set[str] = set()
        offline: set[str] = set()
        unknown = 0
        for resource in resources:
            capability = capability_of(resource, capability_extra)
            if capability is None:
                unknown += 1
            elif resource.avail:
                online.add(capability)
            else:
                offline.add(capability)
        offline -= online
        caps = sorted(online | offline)
        if caps:
            kv("Chips", ", ".join(f"{abbrev(c)}={c}" for c in caps))
        if offline:
            kv("Offline", ", ".join(f"{abbrev(c)}={c}" for c in sorted(offline)), value_style="red")
        if unknown:
            kv("Unknown", f"{unknown} resource(s) of unrecognized class", value_style="yellow")

        # Tags and comment
        if place.tags:
            kv("Tags", ", ".join(f"{k}={v}" for k, v in sorted(place.tags.items())))
        if place.aliases:
            kv("Aliases", ", ".join(place.aliases))
        if place.comment:
            kv("Comment", place.comment)
        text.append("\n")

        # Resources
        narrow = is_narrow(self.app.size)
        if resources:
            text.append("Resources\n", style="bold underline")
            plain.append("Resources")
            for resource in resources:
                # Resource.acquired carries the PLACE holding the resource
                # (coordinator wire semantics), not a user. Held by the
                # place being shown is the normal in-use case; held by a
                # different place is the shared-exporter case worth naming.
                if not resource.avail:
                    status, status_style, name_style = "(offline)", "red", "bold red"
                elif resource.acquired == place.name:
                    status, status_style, name_style = "(in use)", "yellow", "bold"
                elif resource.acquired:
                    status = f"(held by {resource.acquired})"
                    status_style, name_style = "magenta", "bold"
                else:
                    status, status_style, name_style = "(online)", "green", "bold"
                if narrow:
                    # A long resource name plus a long "(held by ...)"
                    # status can together exceed a narrow terminal's
                    # width; splitting onto its own indented line lets
                    # each wrap independently instead of the pair
                    # clipping together.
                    text.append(f"  {resource.name}\n", style=name_style)
                    text.append(f"    {status}\n", style=status_style)
                else:
                    text.append(f"  {resource.name} ", style=name_style)
                    text.append(f"{status}\n", style=status_style)
                text.append("    class: ", style="dim")
                text.append(f"{resource.cls}\n")
                plain.append(f"  {resource.name} {status}")
                plain.append(f"    class: {resource.cls}")
                for key, value in sorted(resource.params.items()):
                    text.append(f"    {key}: ", style="dim")
                    text.append(f"{value}\n")
                    plain.append(f"    {key}: {value}")

        body.update(text)
        self._plain_text = "\n".join(plain)

    def action_dismiss_overlay(self) -> None:
        self.dismiss()

    def action_copy_details(self) -> None:
        copy_via_osc52(
            self.app,
            self._plain_text,
            label=f"details for {self.place_name}",
        )
        push_activity = getattr(self.app, "push_activity", None)
        if push_activity is not None:
            push_activity(f"copied details: {self.place_name}")

    def action_copy_via_select(self) -> None:
        """Copy via terminal-native selection (Shift+Enter).

        Falls back to the suspend-and-print path for users on terminals
        that silently drop OSC 52.
        """
        copy_via_suspend(
            self.app,
            self._plain_text,
            label=f"details for {self.place_name}",
        )
        push_activity = getattr(self.app, "push_activity", None)
        if push_activity is not None:
            push_activity(f"copied details: {self.place_name}")

    def action_commands(self) -> None:
        entries = self._entries_for(self.place_name)
        if not entries:
            self.notify("place is gone")
            return
        from labgrid_tui.ui.screens.command_overlay import CommandOverlay

        self.app.push_screen(
            CommandOverlay(
                self.place_name,
                entries,
                self._runner,
                prefix=getattr(self.app, "prefix", None),
            )
        )

    def _entries_for(self, place_name: str) -> list[CommandEntry]:
        app = self.app
        store = getattr(app, "store", None)
        if store is None:
            return []
        place = store.places.get(place_name)
        if place is None:
            return []
        resources = store.resources_of(place)
        return evaluate(
            place,
            resources,
            current_id(),
            getattr(app, "prefix", "labgrid-client"),
            extra_templates=getattr(app, "command_extra", None),
            extra_place=getattr(app, "place_extra", None),
            reservations=store.reservations,
        )

    def update_guidance(self, guidance: TourGuidance | None) -> None:
        """Tour sideband: show this step's card beside the dialog box."""
        self._guidance = guidance
        self.refresh_fleet()
        refresh_modal_card(self, guidance, DIALOG)
