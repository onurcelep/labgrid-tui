"""Copy-only ActionRunner for the tour.

Every dispatch copies (with the normal toast), never executes:
``client_available()`` is irrelevant here, since nothing this runner does
depends on it. Each copy is also reported to the tour's step sequencer, the
one completion trigger several tour steps share.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from labgrid_tui.model.commands import CommandEntry, EntryState
from labgrid_tui.model.events import Kind
from labgrid_tui.ui.actions import CliActionRunner


class TourActionRunner(CliActionRunner):
    def __init__(
        self,
        *,
        copy_to_clipboard: Callable[[str], None],
        spawn: Callable[[Coroutine[Any, Any, None]], None],
        activity: Callable[[Kind, str, str], None],
        output: Callable[[str], None],
        notify: Callable[[str], None],
        on_copy: Callable[[CommandEntry], None],
    ) -> None:
        super().__init__(
            copy_to_clipboard=copy_to_clipboard,
            spawn=spawn,
            activity=activity,
            output=output,
            notify=notify,
        )
        self._on_copy = on_copy

    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        # Same RUNNABLE guard CliActionRunner.run enforces: dashboard verb
        # dispatch already pre-filters to RUNNABLE entries, but run_entry
        # (reachable from the command palette) does not, and this runner
        # must never copy a greyed-out entry's unresolved command line.
        if entry.state is not EntryState.RUNNABLE:
            return
        self.copy(entry)

    def copy(self, entry: CommandEntry, note: str | None = None) -> None:
        super().copy(entry, note)
        self._on_copy(entry)
