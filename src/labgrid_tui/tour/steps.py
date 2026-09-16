"""Step sequencer for `labgrid-tui tour`.

Each step says one thing worth knowing and the key that shows it. The
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
    CUE_MINE_ACQUIRED,
    CUE_MINE_ACQUIRES,
    CUE_MINE_ALLOCATED,
    CUE_MINE_QUEUED,
    CUE_SERIAL_OFFLINE,
    CUE_SERIAL_ONLINE,
)
from labgrid_tui.ui.guidance import POINT_LOG, POINT_STATUS, POINT_TABLE

STEP_TEXT: tuple[str, ...] = (
    "Every bench, its status dot, who holds it, what it offers. j/k moves.",
    "r gets the bench under the cursor: queue if needed, then acquire, as one copied line.",
    "c lists every command for this bench; greyed ones say why. Enter copies one.",
    "Enter opens the bench: resources, tags, comment. Esc closes.",
    "The lab moved: alice took bench-03, bench-05 lost its serial port. "
    "The log shows it. n continues.",
    "Move to bench-03, alice's, and press r. It queues you; when alice releases, "
    "the same line acquires it. Watch the log.",
    "Team recipes: c, then Right to the robot tab. This bench's values are filled in. "
    "Enter copies one.",
    "shift+p lists your coordinators. Pick desk.",
)
STEP_COUNT = len(STEP_TEXT)
DONE_TEXT = "End of the tour. Try it on your lab: labgrid-tui -x host:20408. q quits."
DONE_TITLE = "TOUR done"

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
# Fired after the step is entered, at multiples of the app's delay unit:
# alice releases bench-03 and my queued reservation is allocated, then
# the copied one-liner completes and the bench is mine. The user watches
# the log tell that story while reading the next step.
DELAYED_ENTRY_CUES: dict[int, tuple[tuple[int, str], ...]] = {
    _STEP_ROBOT_PACK: ((1, CUE_MINE_ALLOCATED), (2, CUE_MINE_ACQUIRED)),
}

# Where the pointer sits while a step is active: on the dashboard (the
# cursor row of the table, the activity log, the status bar) and inside the
# modal the step opens, in that screen's own terms (see TourGuidance).
DASHBOARD_TARGET: dict[int, str] = {
    _STEP_MOVE: POINT_TABLE,
    _STEP_ACQUIRE: POINT_TABLE,
    _STEP_COMMANDS_COPY: POINT_TABLE,
    _STEP_DETAIL: POINT_TABLE,
    _STEP_LAB_CHANGED: POINT_LOG,
    _STEP_QUEUE: POINT_TABLE,
    _STEP_ROBOT_PACK: POINT_TABLE,
    _STEP_COORDINATORS: POINT_STATUS,
}
MODAL_TARGET: dict[int, str] = {
    _STEP_COMMANDS_COPY: "*",
    _STEP_DETAIL: "title",
    _STEP_QUEUE: "Manage",
    _STEP_ROBOT_PACK: "robot",
    _STEP_COORDINATORS: "desk",
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
        return STEP_TEXT[self.step]

    def title(self) -> str:
        if self.done:
            return DONE_TITLE
        return f"TOUR {self.step + 1}/{STEP_COUNT}"

    def dashboard_target(self) -> str | None:
        return None if self.done else DASHBOARD_TARGET.get(self.step)

    def modal_target(self) -> str | None:
        return None if self.done else MODAL_TARGET.get(self.step)

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
