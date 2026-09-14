"""Execution seam: how a chosen CommandEntry becomes an action.

ActionRunner is the one protocol a downstream shell implements to change
execution (credential preflight, direct API calls) while reusing every
widget and the validity engine unchanged. CliActionRunner is the OSS
implementation and carries the v1 semantics extracted from the App.
"""

from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from labgrid_tui.exec_.runner import client_available, run_capture
from labgrid_tui.model.commands import CommandEntry, EntryState
from labgrid_tui.model.events import KIND_ERROR, KIND_NEUTRAL, Kind


class ActionRunner(Protocol):
    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None: ...

    def copy(self, entry: CommandEntry) -> None: ...


class CliActionRunner:
    """Copy-only when the entry needs arguments or labgrid-client is
    missing. Interactive entries (console, ssh, video, audio) are copied
    with a notice: a session that owns the terminal belongs in its own
    terminal, never under a suspended TUI. Everything else runs captured,
    streaming lines to the output sink."""

    def __init__(
        self,
        *,
        copy_to_clipboard: Callable[[str], None],
        spawn: Callable[[Coroutine[Any, Any, None]], None],
        activity: Callable[[Kind, str, str], None],
        output: Callable[[str], None],
        notify: Callable[[str], None],
    ) -> None:
        self._copy_to_clipboard = copy_to_clipboard
        self._spawn = spawn
        self._activity = activity
        self._output = output
        self._notify = notify

    def copy(self, entry: CommandEntry, note: str | None = None) -> None:
        # Every copy toasts: the activity log is hidden by default, so the
        # log line alone would leave the user without feedback.
        self._copy_to_clipboard(entry.command_line)
        self._activity(KIND_NEUTRAL, "", f"copied: {entry.command_line}")
        self._notify(f"Copied: {entry.template.cli_suffix}" + (f" - {note}" if note else ""))

    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        if entry.state is not EntryState.RUNNABLE:
            return
        if entry.template.copy_only:
            # Pack entries: copy unconditionally, regardless of any flag a
            # pack file's own entry might carry: the TUI never shells
            # out to a command it did not define itself.
            self.copy(entry)
            return
        if not client_available():
            self.copy(entry, "labgrid-client not on PATH, run it elsewhere")
            return
        if entry.template.needs_args:
            self.copy(entry, "fill in the <placeholders> and run it in another terminal")
            return
        if entry.template.interactive:
            self.copy(entry, "interactive, run it in another terminal")
            return
        self._activity(KIND_NEUTRAL, "", f"$ {entry.command_line}")
        self._spawn(self._capture(entry, notify_success=notify_success))

    async def _capture(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        code = await run_capture(entry.command_line, self._output)
        kind = KIND_ERROR if code != 0 else KIND_NEUTRAL
        self._activity(kind, "", f"[exit {code}] {entry.command_line}")
        if code != 0:
            self._notify(f"command failed [exit {code}]: {entry.command_line}")
        elif notify_success:
            self._notify(f"command succeeded: {entry.command_line}")
