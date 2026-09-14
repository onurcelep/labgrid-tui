"""Unit tests for TourController: no Textual, no asyncio, no timing.

Every public on_*/skip method is checked against the invariant documented
on TourController itself: it advances the step counter by at most one, and
only when its own guard matches the *current* step. These are the fast,
deterministic counterpart to the pilot tests in tests/ui/test_tour.py,
which additionally prove the triggers are reachable by keyboard.
"""

from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState
from labgrid_tui.tour.steps import DONE_TEXT, STEP_COUNT, TourController

ALL_TRIGGERS_SMOKE: tuple[str, ...] = (
    "skip",
    "on_cursor_changed",
    "on_marks_changed",
    "on_detail_open",
    "on_detail_close",
    "on_alice_acquire",
)


def _acquire_entry() -> CommandEntry:
    template = CommandTemplate("Manage", "Acquire", "acquire")
    return CommandEntry(template, "labgrid-client -p bench-01 acquire", EntryState.RUNNABLE, None)


def _robot_entry() -> CommandEntry:
    template = CommandTemplate("robot", "Smoke tests", "robot tests/smoke", copy_only=True)
    return CommandEntry(template, "robot tests/smoke", EntryState.RUNNABLE, None)


def _other_entry() -> CommandEntry:
    template = CommandTemplate("Info", "Device info", "show")
    return CommandEntry(template, "labgrid-client -p bench-01 show", EntryState.RUNNABLE, None)


def test_label_formats_current_step_one_based() -> None:
    controller = TourController()
    assert controller.label() == "Step 1/8: move the cursor with j/k (or the arrow keys)"
    controller.step = 3
    assert controller.label().startswith("Step 4/8:")


def test_label_is_done_text_once_past_the_last_step() -> None:
    controller = TourController()
    controller.step = STEP_COUNT
    assert controller.label() == DONE_TEXT


def test_on_change_fires_once_per_advance() -> None:
    controller = TourController()
    labels: list[str] = []
    controller.on_change = labels.append
    controller.skip()
    assert labels == [controller.label()]


def test_skip_advances_exactly_one_step_from_any_position() -> None:
    controller = TourController()
    for expected in range(1, STEP_COUNT + 1):
        controller.skip()
        assert controller.step == expected
    # Already done: skip() is a no-op, not a step past STEP_COUNT.
    controller.skip()
    assert controller.step == STEP_COUNT


def test_cursor_changed_ignores_the_auto_highlight_freebie() -> None:
    """DataTable posts one CursorChanged the moment the fleet first
    populates, with no key pressed; that must not complete step 1 on its
    own."""
    controller = TourController()
    controller.on_cursor_changed()  # the freebie
    assert controller.step == 0
    controller.on_cursor_changed()  # a real j/k press
    assert controller.step == 1


def test_cursor_changed_is_a_noop_once_step_1_is_behind_us() -> None:
    controller = TourController()
    controller.step = 3
    controller.on_cursor_changed()
    controller.on_cursor_changed()
    assert controller.step == 3


def test_marks_changed_advances_only_on_step_2() -> None:
    controller = TourController()
    controller.on_marks_changed()  # step 0: not step 2's trigger
    assert controller.step == 0
    controller.step = 1
    controller.on_marks_changed()
    assert controller.step == 2
    controller.on_marks_changed()  # already past: no-op
    assert controller.step == 2


def test_copy_acquire_advances_step_3_only_for_the_acquire_entry() -> None:
    controller = TourController()
    controller.step = 2
    controller.on_copy(_other_entry())
    assert controller.step == 2  # wrong entry: not the trigger
    controller.on_copy(_acquire_entry())
    assert controller.step == 3


def test_copy_advances_step_4_for_any_entry() -> None:
    controller = TourController()
    controller.step = 3
    controller.on_copy(_other_entry())
    assert controller.step == 4


def test_copy_robot_advances_step_7_only_for_robot_category() -> None:
    controller = TourController()
    controller.step = 6
    controller.on_copy(_other_entry())
    assert controller.step == 6  # not the robot pack: not the trigger
    controller.on_copy(_robot_entry())
    assert controller.step == 7


def test_copy_off_its_steps_is_a_noop() -> None:
    controller = TourController()
    controller.step = 4  # step 5 (detail): copy is not its trigger
    controller.on_copy(_acquire_entry())
    controller.on_copy(_robot_entry())
    assert controller.step == 4


def test_detail_open_then_close_advances_step_5() -> None:
    controller = TourController()
    controller.step = 4
    controller.on_detail_close()  # close without ever opening: no-op
    assert controller.step == 4
    controller.on_detail_open()
    assert controller.step == 4  # opening alone does not advance
    controller.on_detail_close()
    assert controller.step == 5


def test_detail_hooks_off_step_5_are_a_noop() -> None:
    controller = TourController()
    controller.on_detail_open()
    controller.on_detail_close()
    assert controller.step == 0


def test_alice_acquire_advances_step_6_only_while_current() -> None:
    controller = TourController()
    controller.on_alice_acquire()  # lands early, long before step 6
    assert controller.step == 0
    controller.step = 5
    controller.on_alice_acquire()
    assert controller.step == 6


def test_alice_acquire_does_not_replay_once_past_step_6() -> None:
    controller = TourController()
    controller.step = 5
    controller.on_alice_acquire()
    controller.on_alice_acquire()  # a second delivery, if it ever happened
    assert controller.step == 6


def test_coordinator_switch_needs_desk_then_lab_while_on_step_8() -> None:
    controller = TourController()
    controller.step = 7
    controller.on_coordinator_switch("lab")  # already active: not a switch
    assert controller.step == 7
    controller.on_coordinator_switch("desk")
    assert controller.step == 7  # halfway through the round trip
    controller.on_coordinator_switch("lab")
    assert controller.step == 8


def test_coordinator_switch_off_step_8_is_a_noop() -> None:
    controller = TourController()
    controller.on_coordinator_switch("desk")
    controller.on_coordinator_switch("lab")
    assert controller.step == 0


def test_every_trigger_advances_by_at_most_one_step_from_any_position() -> None:
    """The invariant tour/steps.py documents, swept across every step
    index: no trigger call may ever move the counter by more than one."""
    entries = (_acquire_entry(), _robot_entry(), _other_entry())
    for start in range(STEP_COUNT + 1):
        for name in ALL_TRIGGERS_SMOKE:
            controller = TourController()
            controller.step = start
            getattr(controller, name)()
            assert controller.step in (start, start + 1)
        for entry in entries:
            controller = TourController()
            controller.step = start
            controller.on_copy(entry)
            assert controller.step in (start, start + 1)
        for name in ("desk", "lab"):
            controller = TourController()
            controller.step = start
            controller.on_coordinator_switch(name)
            assert controller.step in (start, start + 1)
