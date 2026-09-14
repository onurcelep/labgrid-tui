"""Modal help overlay: tabbed key bindings, operations, and reference.

Reachable with ``?`` from anywhere in the dashboard. Each tab's body is a
``VerticalScroll`` that is focused on mount and on every tab switch, so
scrolling works immediately without discovering the Tab key.
"""

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static, Tab, TabbedContent, TabPane, Tabs

import labgrid_tui
from labgrid_tui.ui.layout import is_narrow
from labgrid_tui.ui.widgets.help_text import (
    TAB_ACTIVITY_LOG,
    TAB_KEY_BINDINGS,
    TAB_OPERATIONS,
    TAB_TABLE_REFERENCE,
)

_FULL_TAB_TITLES = ("Key Bindings", "Operations", "Table Reference", "Activity Log")
_NARROW_TAB_TITLES = ("Keys", "Ops", "Table", "Log")

_FULL_HINT = "left/right = tabs | j/k/pageup/pagedown/g/G = scroll | esc/?/q = close"
_NARROW_HINT = "esc close"


class HelpOverlay(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpOverlay { align: center middle; }
    #help-modal {
        width: 95%;
        max-width: 100;
        height: 90%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #help-title {
        text-style: bold;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    #help-modal > TabbedContent { height: 1fr; }
    #help-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        text-align: center;
        width: 100%;
    }

    HelpOverlay.-narrow #help-modal { width: 100%; height: 100%; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_overlay", "Close", show=True),
        Binding("question_mark", "dismiss_overlay", "Close", show=False),
        Binding("q", "dismiss_overlay", "Close", show=False),
        # Screen-level so tab switching and scrolling work while the pane's
        # VerticalScroll holds focus: App-level BINDINGS never reach a
        # ModalScreen in Textual 8.2.8, so these can't be inherited.
        # left/right only reach the screen because each pane scroller has no
        # horizontal overflow (its own left/right actions skip); giving a pane
        # overflow-x would silently turn these keys into horizontal scrolling.
        Binding("right,l", "next_tab", "Next tab", show=False),
        Binding("left,h", "prev_tab", "Prev tab", show=False),
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

    def compose(self) -> ComposeResult:
        with Vertical(id="help-modal"):
            yield Static(
                f"labgrid-tui Help [dim]v{labgrid_tui.__version__}[/dim]",
                id="help-title",
            )
            with TabbedContent(
                "Key Bindings", "Operations", "Table Reference", "Activity Log",
            ):
                with TabPane("Key Bindings"), VerticalScroll():
                    yield Markdown(TAB_KEY_BINDINGS)
                with TabPane("Operations"), VerticalScroll():
                    yield Markdown(TAB_OPERATIONS)
                with TabPane("Table Reference"), VerticalScroll():
                    yield Markdown(TAB_TABLE_REFERENCE)
                with TabPane("Activity Log"), VerticalScroll():
                    yield Markdown(TAB_ACTIVITY_LOG)
            yield Static(_FULL_HINT, id="help-hint")

    def on_mount(self) -> None:
        self._focus_active_pane()
        self._apply_narrow_layout()

    def on_resize(self, _event: events.Resize) -> None:
        self._apply_narrow_layout()

    def _apply_narrow_layout(self) -> None:
        narrow = is_narrow(self.app.size)
        titles = _NARROW_TAB_TITLES if narrow else _FULL_TAB_TITLES
        tabs = list(self.query_one(TabbedContent).query_one(Tabs).query(Tab))
        for tab, title in zip(tabs, titles, strict=False):
            tab.label = title
        self.query_one("#help-hint", Static).update(_NARROW_HINT if narrow else _FULL_HINT)

    def on_tabbed_content_tab_activated(self, _event: TabbedContent.TabActivated) -> None:
        self._focus_active_pane()

    def _active_pane_scroll(self) -> VerticalScroll | None:
        pane = self.query_one(TabbedContent).active_pane
        if pane is None:
            return None
        return pane.query_one(VerticalScroll)

    def _focus_active_pane(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            self.set_focus(scroll)

    def action_dismiss_overlay(self) -> None:
        self.dismiss()

    def action_next_tab(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).action_next_tab()

    def action_prev_tab(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).action_previous_tab()

    def action_scroll_body_down(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            scroll.scroll_down(animate=False)

    def action_scroll_body_up(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            scroll.scroll_up(animate=False)

    def action_scroll_body_page_down(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            scroll.scroll_page_down(animate=False)

    def action_scroll_body_page_up(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            scroll.scroll_page_up(animate=False)

    def action_scroll_body_home(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            scroll.scroll_home(animate=False)

    def action_scroll_body_end(self) -> None:
        scroll = self._active_pane_scroll()
        if scroll is not None:
            scroll.scroll_end(animate=False)
