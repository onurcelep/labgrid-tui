"""Modal per-device command overlay: one tab per category, filter, copy.

Left/right switch tabs at the screen level, and the active pane's list is
focused on every switch so up/down navigation works immediately without
discovering the Tab key. The overlay is copy-only: it never executes a
command itself; the dashboard's verb keys are the only executors.
"""

import re
from dataclasses import replace

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static, TabbedContent, TabPane, Tabs
from textual.widgets.option_list import Option

from labgrid_tui.model.commands import GROUP_ORDER, CommandEntry, EntryState
from labgrid_tui.model.flags import flags_hint
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.clipboard import copy_via_suspend

# Keys the option list / screen keep handling themselves; everything else
# that looks like text entry is forwarded to the filter input (see on_key).
# ``q``/``c`` close the overlay so they must never reach the filter, even
# though they are otherwise printable characters.
_LIST_OWNED_KEYS = frozenset(
    {
        "up",
        "down",
        "left",
        "right",
        "enter",
        "y",
        "escape",
        "q",
        "c",
        "shift+enter",
        "home",
        "end",
        "pageup",
        "pagedown",
        "tab",
        "shift+tab",
    }
)


def _slug(category: str) -> str:
    return re.sub(r"[^a-z0-9-]", "-", category.lower())


def _prompt_for(entry: CommandEntry) -> Text:
    """Two-line option prompt: bold label, dim command line beneath.

    Unavailable entries get a dimmed label (with the reason appended) and
    a struck-through command line so they read as de-emphasized without
    disappearing from the list.
    """
    text = Text()
    if entry.state is EntryState.RUNNABLE:
        text.append(entry.template.label, style="bold")
        cmd_style = "dim"
    else:
        text.append(f"{entry.template.label}  ({entry.reason})", style="dim")
        cmd_style = "dim strike"
    text.append("\n")
    text.append(f"  {entry.command_line}", style=cmd_style)
    return text


class CommandOverlay(ModalScreen[None]):
    DEFAULT_CSS = """
    CommandOverlay { align: center middle; }
    #overlay-body {
        width: 80%;
        max-width: 110;
        height: 70%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    #overlay-title { text-style: bold; }
    #overlay-hint {
        height: auto;
        margin-top: 1;
        color: $text-muted;
        text-align: center;
        width: 100%;
    }
    #overlay-edit { margin-top: 1; }
    #overlay-edit-hint { color: $text-muted; height: auto; }
    #overlay-tabs { height: 1fr; }

    CommandOverlay.-narrow #overlay-body { width: 100%; height: 90%; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_overlay", "Close", show=False),
        Binding("q", "dismiss_overlay", "Close", show=False),
        Binding("c", "dismiss_overlay", "Close", show=False),
        Binding("y", "copy_selected", "Copy", show=False),
        # Screen-level so left/right work while the list holds focus. tab/
        # shift+tab used to cycle focus between the filter Input and the
        # OptionList (Screen's own default binding); the key-forwarding
        # design in on_key makes that cycling unnecessary, so they are
        # reassigned to tabs here, like left/right.
        Binding("right,tab", "next_tab", "Next tab", show=False),
        Binding("left,shift+tab", "prev_tab", "Prev tab", show=False),
        # priority=True: App's own ctrl+p is a priority binding for the
        # command palette and would otherwise win before this screen-level
        # one is even checked (see LabgridTuiApp.check_action, which
        # disables that App binding while this screen is on top).
        Binding("ctrl+n", "list_cursor_down", "Next item", show=False, priority=True),
        Binding("ctrl+p", "list_cursor_up", "Prev item", show=False, priority=True),
        Binding("ctrl+e", "edit_selected", "Edit", show=False),
        # Suspend-and-print fallback for terminals that drop OSC 52.
        Binding("shift+enter", "copy_via_select", "Copy via select", show=True),
    ]

    def __init__(
        self,
        place_name: str,
        entries: list[CommandEntry],
        runner: ActionRunner,
        category: str | None = None,
        prefix: str | None = None,
    ) -> None:
        super().__init__()
        self.place_name = place_name
        self._runner = runner
        self._initial_category = category
        self._prefix = prefix
        # Category -> entries, tabs ordered by GROUP_ORDER; categories it
        # does not know (plugins, config) follow in first-appearance order.
        grouped: dict[str, list[CommandEntry]] = {}
        for entry in entries:
            grouped.setdefault(entry.template.category, []).append(entry)
        rank = {name: i for i, name in enumerate(GROUP_ORDER)}
        self._by_category: dict[str, list[CommandEntry]] = dict(
            sorted(grouped.items(), key=lambda kv: rank.get(kv[0], len(rank)))
        )
        # Slug -> currently visible (filtered) entries per tab.
        self._visible: dict[str, list[CommandEntry]] = {}
        self._edit_entry: CommandEntry | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="overlay-body"):
            yield Static(f"commands: {self.place_name}", id="overlay-title")
            yield Input(id="overlay-filter", placeholder="type to filter")
            with TabbedContent(id="overlay-tabs"):
                for category in self._by_category:
                    slug = _slug(category)
                    with TabPane(category, id=f"tab-{slug}"):
                        yield OptionList(id=f"overlay-list-{slug}")
            yield Static(
                "Enter = copy | Shift+Enter = copy via select | "
                "Ctrl+E = edit | Left/Right = tabs | Esc = close",
                id="overlay-hint",
            )
            edit = Input(id="overlay-edit", placeholder="edit command, enter copies")
            edit.add_class("hidden")
            yield edit
            hint = Static("", id="overlay-edit-hint")
            hint.add_class("hidden")
            yield hint

    def on_mount(self) -> None:
        self._apply_filter("")
        if self._initial_category and self._initial_category in self._by_category:
            self.query_one(TabbedContent).active = f"tab-{_slug(self._initial_category)}"
        self._focus_active_list()

    # ------------------------------------------------------------------
    # Tabs
    # ------------------------------------------------------------------

    def _active_slug(self) -> str | None:
        active = self.query_one(TabbedContent).active
        return active.removeprefix("tab-") if active else None

    def _active_list(self) -> OptionList | None:
        slug = self._active_slug()
        if slug is None:
            return None
        return self.query_one(f"#overlay-list-{slug}", OptionList)

    def _focus_active_list(self) -> None:
        options = self._active_list()
        if options is None:
            return
        options.focus()
        if options.highlighted is None and options.option_count:
            options.highlighted = 0

    def action_next_tab(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).action_next_tab()

    def action_prev_tab(self) -> None:
        self.query_one(TabbedContent).query_one(Tabs).action_previous_tab()

    def on_tabbed_content_tab_activated(self, event: TabbedContent.TabActivated) -> None:
        # Focus follows the tab so up/down navigate the new list at once.
        self._focus_active_list()

    def action_list_cursor_down(self) -> None:
        if self._editing():
            return  # the edit input owns the keyboard while open
        options = self._active_list()
        if options is not None:
            options.action_cursor_down()

    def action_list_cursor_up(self) -> None:
        if self._editing():
            return  # the edit input owns the keyboard while open
        options = self._active_list()
        if options is not None:
            options.action_cursor_up()

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------

    def _apply_filter(self, query: str) -> None:
        q = query.strip().lower()
        for category, entries in self._by_category.items():
            slug = _slug(category)
            visible = [
                entry
                for entry in entries
                if q in f"{entry.template.label} {entry.command_line}".lower()
            ]
            self._visible[slug] = visible
            options = self.query_one(f"#overlay-list-{slug}", OptionList)
            options.clear_options()
            for entry in visible:
                options.add_option(Option(_prompt_for(entry)))
            if visible:
                options.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._apply_filter(event.value)

    def on_key(self, event: events.Key) -> None:
        # A list holds focus (arrow-key navigation needs it), so typed
        # characters would otherwise never reach #overlay-filter. Forward
        # printable characters and backspace by hand; leave navigation,
        # selection, and dismiss keys untouched.
        if event.key in _LIST_OWNED_KEYS:
            return
        if self._editing():
            return  # the edit input owns the keyboard while open
        filter_input = self.query_one("#overlay-filter", Input)
        if event.key == "backspace":
            filter_input.value = filter_input.value[:-1]
            event.stop()
            event.prevent_default()
        elif event.is_printable and event.character:
            filter_input.value += event.character
            event.stop()
            event.prevent_default()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _highlighted(self) -> CommandEntry | None:
        slug = self._active_slug()
        options = self._active_list()
        if slug is None or options is None:
            return None
        index = options.highlighted
        visible = self._visible.get(slug, [])
        if index is None or index >= len(visible):
            return None
        return visible[index]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Enter always copies (OSC 52 + toast), never runs. The
        # dashboard's verb keys are the only executors.
        event.stop()
        slug = self._active_slug()
        if slug is None:
            return
        visible = self._visible.get(slug, [])
        index = event.option_index
        if index < 0 or index >= len(visible):
            return
        entry = visible[index]
        self._runner.copy(entry)
        self.dismiss()

    def _edit_widgets(self) -> tuple[Input, Static]:
        return (
            self.query_one("#overlay-edit", Input),
            self.query_one("#overlay-edit-hint", Static),
        )

    def _editing(self) -> bool:
        return not self.query_one("#overlay-edit", Input).has_class("hidden")

    def action_edit_selected(self) -> None:
        entry = self._highlighted()
        if entry is None:
            return
        self._edit_entry = entry
        edit, hint = self._edit_widgets()
        edit.value = entry.command_line
        hint_text = flags_hint(entry.template.cli_suffix)
        hint.update(
            f"also accepts: {hint_text}" if hint_text else "edit freely; enter copies, esc cancels"
        )
        edit.remove_class("hidden")
        hint.remove_class("hidden")
        edit.focus()

    def _close_editor(self) -> None:
        edit, hint = self._edit_widgets()
        edit.add_class("hidden")
        hint.add_class("hidden")
        edit.value = ""
        self._focus_active_list()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "overlay-edit":
            return
        event.stop()
        entry = self._edit_entry
        line = event.value.strip()
        if entry is None or not line:
            self._close_editor()
            return
        # The edited line is always copied, never run: editing exists so
        # the user can fill in <placeholders> or tweak flags before taking
        # the command elsewhere, not to bypass the overlay's copy-only
        # contract.
        edited = replace(
            entry,
            command_line=line,
            template=replace(entry.template, needs_args="<" in line),
        )
        self._runner.copy(edited)
        self.dismiss()

    def action_copy_selected(self) -> None:
        entry = self._highlighted()
        if entry is not None:
            self._runner.copy(entry)
        self.dismiss()

    def action_copy_via_select(self) -> None:
        """Copy via terminal-native selection (Shift+Enter).

        OSC 52 is silently dropped by libVTE-based terminals on Debian
        and by Konsole with the clipboard toggle off. Suspending the TUI
        and printing the command in plain text lets the user copy with
        the terminal's own selection mechanism, which works everywhere.
        """
        if self._editing():
            return  # the edit input owns the keyboard while open
        entry = self._highlighted()
        if entry is None:
            self.notify("No command selected", severity="warning")
            return
        copy_via_suspend(self.app, entry.command_line, label="command")
        push_activity = getattr(self.app, "push_activity", None)
        if push_activity is not None:
            push_activity(f"copied: {entry.command_line}")
        self.dismiss()

    def action_dismiss_overlay(self) -> None:
        if self._editing():
            self._close_editor()
            return
        self.dismiss()
