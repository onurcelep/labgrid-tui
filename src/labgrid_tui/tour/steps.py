"""Step sequencer for `labgrid-tui tour`.

Each step names one thing worth knowing and the one key that shows it. The
controller advances on hooks from the dashboard, the action runner and the
app; it never binds keys of its own. Entering a step may fire a cue on the
scripted fleet (see ``ENTRY_CUES``) so the lab visibly reacts at the moment
the panel is talking about it.

Invariant: every ``on_*``/``skip`` call advances the step counter by at most
one, and only when its own guard matches the current step. One key press or
one scripted event is one transition, which is what makes the step index
deterministic and worth asserting on directly in tests.
"""

from collections.abc import Callable

from labgrid_tui.model.commands import VERB_ACQUIRE, CommandEntry, is_verb
from labgrid_tui.tour.fleet import (
    CUE_ALICE_ACQUIRES,
    CUE_MINE_ACQUIRES,
    CUE_MINE_ALLOCATED,
    CUE_MINE_QUEUED,
    CUE_SERIAL_OFFLINE,
    CUE_SERIAL_ONLINE,
)

STEP_TEXT: tuple[str, ...] = (
    "Your lab: every bench, its status dot, who holds it, what it offers. Move with j/k.",
    "r gets the bench under the cursor: it queues if needed, then acquires. "
    "The tour only copies the line.",
    "c lists this bench's commands; greyed ones say why. Enter copies one.",
    "Enter on the table opens the bench: resources, tags, comment. Esc closes.",
    "The lab moved: alice took bench-03 and bench-05 lost its serial port. "
    "Watch the dots and the log, then press n.",
    "Move to bench-03 (alice's) and press r: it now reads Queue and acquire. Copy it.",
    "Team recipes: c, then -> to the robot tab. Enter copies one with this bench's "
    "values filled in.",
    "Another lab: shift+p lists your coordinators. Pick desk.",
)
STEP_COUNT = len(STEP_TEXT)
DONE_TEXT = (
    "That is the tour. Point it at your lab: labgrid-tui -x host:20408. "
    "shift+p brings you back; q quits."
)

_STEP_MOVE = 0
_STEP_ACQUIRE = 1
_STEP_COMMANDS_COPY = 2
_STEP_DETAIL = 3
_STEP_LAB_CHANGED = 4
_STEP_QUEUE = 5
_STEP_ROBOT_PACK = 6
_STEP_COORDINATORS = 7

# Cues the fleet plays when a step is entered: the bench the user just got
# becomes theirs; alice and the serial port move right as the panel says
# the lab changed; the port returns while the user queues; the queued
# reservation appears as soon as the user copied the queue line.
ENTRY_CUES: dict[int, tuple[str, ...]] = {
    _STEP_COMMANDS_COPY: (CUE_MINE_ACQUIRES,),
    _STEP_LAB_CHANGED: (CUE_ALICE_ACQUIRES, CUE_SERIAL_OFFLINE),
    _STEP_QUEUE: (CUE_SERIAL_ONLINE,),
    _STEP_ROBOT_PACK: (CUE_MINE_QUEUED,),
}
# Fired a moment after the step is entered: alice hands bench-03 over and
# the queued reservation is allocated while the user reads the next step.
DELAYED_ENTRY_CUES: dict[int, tuple[str, ...]] = {
    _STEP_ROBOT_PACK: (CUE_MINE_ALLOCATED,),
}


class TourController:
    """Tracks the current step and applies each step's one trigger."""

    def __init__(self) -> None:
        self.step = 0
        self.on_change: Callable[[str], None] | None = None
        self.on_enter: Callable[[int], None] | None = None
        self.on_done: Callable[[], None] | None = None
        self._detail_opened = False
        self._initial_place: str | None = None
        # The bench the user got at the acquire step; the fleet makes it theirs.
        self.acquired_place: str | None = None

    @property
    def done(self) -> bool:
        return self.step >= STEP_COUNT

    def label(self) -> str:
        if self.done:
            return DONE_TEXT
        return f"Step {self.step + 1}/{STEP_COUNT}: {STEP_TEXT[self.step]}"

    def _advance(self) -> None:
        self.step += 1
        if self.on_change is not None:
            self.on_change(self.label())
        if self.done:
            if self.on_done is not None:
                self.on_done()
        elif self.on_enter is not None:
            self.on_enter(self.step)

    def skip(self) -> None:
        if not self.done:
            self._advance()

    def on_cursor_changed(self, place: str) -> None:
        # The table highlights its first row on its own when the fleet
        # arrives; only a move to a different bench counts as the user's.
        if self._initial_place is None:
            self._initial_place = place
            return
        if self.step == _STEP_MOVE and place and place != self._initial_place:
            self._advance()

    def on_detail_open(self) -> None:
        if self.step == _STEP_DETAIL:
            self._detail_opened = True

    def on_detail_close(self) -> None:
        if self.step == _STEP_DETAIL and self._detail_opened:
            self._advance()

    def on_copy(self, entry: CommandEntry) -> None:
        template = entry.template
        get_verb = is_verb(template, VERB_ACQUIRE)
        expected = {
            _STEP_ACQUIRE: get_verb and not template.label.startswith("Queue"),
            _STEP_COMMANDS_COPY: not get_verb and template.category != "robot",
            _STEP_QUEUE: get_verb and template.label.startswith("Queue"),
            _STEP_ROBOT_PACK: template.category == "robot",
        }
        if not expected.get(self.step, False):
            return
        if self.step == _STEP_ACQUIRE:
            self.acquired_place = entry.place
        self._advance()

    def on_coordinator_switch(self, name: str) -> None:
        if self.step == _STEP_COORDINATORS and name == "desk":
            self._advance()
