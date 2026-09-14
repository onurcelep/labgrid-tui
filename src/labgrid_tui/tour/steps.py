"""Step sequencer for `labgrid-tui tour`.

Eight steps, each with one completion trigger, driven entirely by hooks
from the dashboard (DashboardScreen's tour_hook), the action runner
(TourActionRunner.copy), and the app (coordinator switch, the scripted
alice-acquires-bench-03 event) instead of any binding of its own. TourPanel
renders whatever label() currently says and re-renders on every advance via
on_change.
"""

from collections.abc import Callable

from labgrid_tui.model.commands import CommandEntry

STEP_TEXT: tuple[str, ...] = (
    "move the cursor with j/k (or the arrow keys)",
    "mark the bench under your cursor with space",
    "acquire the bench under your cursor with r",
    "press c to open commands, then enter to copy one",
    "press d (or enter) to open the detail view, then esc to close it",
    "watch the activity log for alice's bench (press a if it is hidden)",
    "press c then -> to open the robot pack tab, then enter to copy an entry",
    "press shift+p, switch to desk, then switch back to lab",
)
STEP_COUNT = len(STEP_TEXT)
DONE_TEXT = "Done. Run: labgrid-tui -x your-coordinator:20408"

# Step indices (0-based) referenced by name below, so the trigger logic
# reads by intent rather than by raw number.
_STEP_ACQUIRE = 2
_STEP_COMMANDS_COPY = 3
_STEP_DETAIL = 4
_STEP_ACTIVITY = 5
_STEP_ROBOT_PACK = 6
_STEP_COORDINATORS = 7


class TourController:
    """Tracks the current step and applies each step's one trigger.

    Not a Textual widget or message system on its own: TourPanel owns
    ``on_change`` and re-renders whenever it fires.
    """

    def __init__(self) -> None:
        self.step = 0
        self.on_change: Callable[[str], None] | None = None
        self._alice_landed = False
        self._detail_opened = False
        self._seen_desk = False
        # DataTable auto-highlights row 0 the moment the fleet first
        # populates, posting one CursorChanged with no key ever pressed;
        # the first call here is that freebie, not step 1's own trigger.
        self._cursor_events_seen = 0

    def label(self) -> str:
        if self.step >= STEP_COUNT:
            return DONE_TEXT
        return f"Step {self.step + 1}/{STEP_COUNT}: {STEP_TEXT[self.step]}"

    def _advance(self) -> None:
        self.step += 1
        if self.step == _STEP_ACTIVITY and self._alice_landed:
            # The scripted event already landed before the tour reached
            # this step: don't make the user wait for something that
            # already happened.
            self._advance()
            return
        self._notify()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change(self.label())

    def skip(self) -> None:
        if self.step < STEP_COUNT:
            self._advance()

    def on_cursor_changed(self) -> None:
        self._cursor_events_seen += 1
        if self.step == 0 and self._cursor_events_seen > 1:
            self._advance()

    def on_marks_changed(self) -> None:
        if self.step == 1:
            self._advance()

    def on_copy(self, entry: CommandEntry) -> None:
        if self.step == _STEP_ACQUIRE:
            if entry.template.category == "Manage" and entry.template.label == "Acquire":
                self._advance()
            return
        if self.step == _STEP_COMMANDS_COPY:
            self._advance()
            return
        if self.step == _STEP_ROBOT_PACK and entry.template.category == "robot":
            self._advance()

    def on_detail_open(self) -> None:
        if self.step == _STEP_DETAIL:
            self._detail_opened = True

    def on_detail_close(self) -> None:
        if self.step == _STEP_DETAIL and self._detail_opened:
            self._advance()

    def on_alice_acquire(self) -> None:
        self._alice_landed = True
        if self.step == _STEP_ACTIVITY:
            self._advance()

    def on_coordinator_switch(self, name: str) -> None:
        if self.step != _STEP_COORDINATORS:
            return
        if name == "desk":
            self._seen_desk = True
        elif name == "lab" and self._seen_desk:
            self._advance()
