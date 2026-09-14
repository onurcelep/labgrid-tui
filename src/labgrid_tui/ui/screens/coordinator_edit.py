"""Create/edit modal for a named coordinator, built from field specs.

Field-spec driven (not a fixed name/address/prefix layout) so a shell
built on labgrid-tui can append its own fields (auth, ssh, ...) without
forking this screen: see the "Reuse contract" in the project's design
notes and the README's "Building on labgrid-tui" section. Extra values
land in ``CoordinatorEntry.extra`` on the caller's side; this screen only
ever deals in field key/value strings.
"""

from collections.abc import Callable
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    placeholder: str = ""
    # Returns the normalized value, or raises (any exception; its str() is
    # shown inline) to reject the submitted value.
    validator: Callable[[str], str] | None = None


class CoordinatorEditModal(ModalScreen[dict[str, str] | None]):
    """Dismisses with ``{key: value, ...}`` for every field on submit
    (validators applied), or ``None`` on cancel."""

    DEFAULT_CSS = """
    CoordinatorEditModal { align: center middle; }
    #coord-edit-modal {
        width: 70;
        height: auto;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #coord-edit-title {
        text-style: bold;
        width: 100%;
        content-align: center middle;
        margin-bottom: 1;
    }
    .coord-edit-label { margin-top: 1; }
    #coord-edit-modal > Input { margin-bottom: 1; }
    #coord-edit-error { color: $error; height: auto; }
    #coord-edit-hint {
        margin-top: 1;
        color: $text-muted;
        text-align: center;
        width: 100%;
    }

    CoordinatorEditModal.-narrow #coord-edit-modal { width: 100%; }
    """

    BINDINGS = [
        Binding("escape", "dismiss_none", "Cancel", show=False),
    ]

    def __init__(
        self,
        title: str,
        fields: list[FieldSpec],
        initial: dict[str, str] | None = None,
        locked_keys: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__()
        self._title = title
        self._fields = fields
        self._initial = initial or {}
        self._locked_keys = locked_keys

    def _input_id(self, key: str) -> str:
        return f"coord-edit-{key}"

    def compose(self) -> ComposeResult:
        with Vertical(id="coord-edit-modal"):
            yield Static(self._title, id="coord-edit-title")
            for spec in self._fields:
                yield Static(f"{spec.label}:", classes="coord-edit-label")
                yield Input(
                    value=self._initial.get(spec.key, ""),
                    placeholder=spec.placeholder,
                    id=self._input_id(spec.key),
                    disabled=spec.key in self._locked_keys,
                )
            yield Static("", id="coord-edit-error")
            yield Static(
                "Tab = next field | Enter = save | Escape = cancel",
                id="coord-edit-hint",
            )

    def on_mount(self) -> None:
        for spec in self._fields:
            if spec.key not in self._locked_keys:
                self.query_one(f"#{self._input_id(spec.key)}", Input).focus()
                return

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter moves to the next editable field, or submits from the last.
        ids = [self._input_id(spec.key) for spec in self._fields]
        if event.input.id not in ids:
            return
        current_index = ids.index(event.input.id)
        for next_index in range(current_index + 1, len(self._fields)):
            if self._fields[next_index].key not in self._locked_keys:
                self.query_one(f"#{ids[next_index]}", Input).focus()
                return
        self._submit()

    def _submit(self) -> None:
        values: dict[str, str] = {}
        error = self.query_one("#coord-edit-error", Static)
        error.update("")
        for spec in self._fields:
            raw = self.query_one(f"#{self._input_id(spec.key)}", Input).value.strip()
            if spec.validator is not None and spec.key not in self._locked_keys:
                try:
                    raw = spec.validator(raw)
                except Exception as exc:
                    error.update(str(exc))
                    self.query_one(f"#{self._input_id(spec.key)}", Input).focus()
                    return
            values[spec.key] = raw
        self.dismiss(values)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)
