"""Unit tests for TourController: no Textual, no asyncio, no timing.

Every public on_*/skip method is checked against the invariant documented
on TourController itself: it advances the step counter by at most one, and
only when its own guard matches the current step.
"""

from labgrid_tui.model.commands import GET, CommandEntry, CommandTemplate, EntryState
from labgrid_tui.tour.fleet import (
    CUE_ALICE_ACQUIRES,
    CUE_MINE_ACQUIRED,
    CUE_MINE_ACQUIRES,
    CUE_MINE_ALLOCATED,
    CUE_MINE_QUEUED,
    CUE_SERIAL_ONLINE,
)
from labgrid_tui.tour.steps import (
    DELAYED_ENTRY_CUES,
    DONE_TEXT,
    ENTRY_CUES,
    STEP_COUNT,
    TourController,
)


def _entry(category: str, label: str, place: str = "bench-01") -> CommandEntry:
    get = label in ("Acquire", "Queue and acquire")
    template = CommandTemplate(
        category,
        label,
        label.lower(),
        requires=GET if get else frozenset(),
        copy_only=(category == "robot" or get),
    )
    return CommandEntry(template, f"{category} {label}", EntryState.RUNNABLE, None, place=place)


def _at(step: int) -> TourController:
    controller = TourController()
    for _ in range(step):
        controller.skip()
    return controller


def test_title_is_one_based_and_label_names_the_key_without_preamble() -> None:
    controller = TourController()
    assert controller.title() == "TOUR 1/8"
    assert not controller.label().startswith("Look at")
    assert "j/k" in controller.label()
    assert controller.dashboard_marker() == "table"
    assert controller.modal_target() is None


def test_no_step_text_opens_with_look_at() -> None:
    for step in range(STEP_COUNT):
        assert not _at(step).label().startswith("Look at")


def test_done_after_the_last_step() -> None:
    controller = _at(STEP_COUNT)
    assert controller.done
    assert controller.label() == DONE_TEXT
    controller.skip()
    assert controller.step == STEP_COUNT


def test_on_change_and_on_enter_fire_once_per_advance_and_on_done_at_the_end() -> None:
    controller = TourController()
    changes: list[str] = []
    entered: list[int] = []
    done: list[bool] = []
    controller.on_change = changes.append
    controller.on_enter = entered.append
    controller.on_done = lambda: done.append(True)
    for _ in range(STEP_COUNT):
        controller.skip()
    assert len(changes) == STEP_COUNT
    assert entered == list(range(1, STEP_COUNT))
    assert done == [True]


def test_cursor_move_counts_only_a_change_from_the_initial_highlight() -> None:
    controller = TourController()
    controller.on_cursor_changed("bench-01")  # the table's own first highlight
    assert controller.step == 0
    controller.on_cursor_changed("bench-01")  # a rebuild re-reporting the same row
    assert controller.step == 0
    controller.on_cursor_changed("bench-02")
    assert controller.step == 1
    controller.on_cursor_changed("bench-03")
    assert controller.step == 1


def test_detail_open_then_close_advances_step_4_only() -> None:
    controller = _at(3)
    controller.on_detail_close()
    assert controller.step == 3
    controller.on_detail_open()
    controller.on_detail_close()
    assert controller.step == 4
    controller.on_detail_open()
    controller.on_detail_close()
    assert controller.step == 4


def test_copy_triggers_match_their_own_step_only() -> None:
    controller = _at(1)
    controller.on_copy(_entry("Info", "Device info"))
    assert controller.step == 1
    controller.on_copy(_entry("Manage", "Queue and acquire"))
    assert controller.step == 1  # step 2 wants a bench that allocates at once
    controller.on_copy(_entry("Manage", "Acquire", place="bench-02"))
    assert controller.step == 2
    assert controller.acquired_place == "bench-02"
    controller.on_copy(_entry("robot", "Smoke tests"))
    assert controller.step == 2  # step 3 wants a built-in entry, not a pack entry
    controller.on_copy(_entry("Manage", "Acquire"))
    assert controller.step == 2  # nor the get verb again
    controller.on_copy(_entry("Connect", "Serial console"))
    assert controller.step == 3
    controller.on_detail_open()
    controller.on_detail_close()
    assert controller.step == 4
    controller.skip()  # step 5 is acknowledged with n
    controller.on_copy(_entry("Manage", "Acquire"))
    assert controller.step == 5  # a free bench does not satisfy the queue step
    controller.on_copy(_entry("Manage", "Queue and acquire", place="bench-03"))
    assert controller.step == 6
    controller.on_copy(_entry("Info", "Device info"))
    assert controller.step == 6
    controller.on_copy(_entry("robot", "Smoke tests"))
    assert controller.step == 7


def test_coordinator_switch_to_desk_finishes_the_tour() -> None:
    controller = _at(STEP_COUNT - 1)
    controller.on_coordinator_switch("lab")
    assert not controller.done
    controller.on_coordinator_switch("desk")
    assert controller.done
    early = _at(3)
    early.on_coordinator_switch("desk")
    assert early.step == 3


def test_entry_cues_fire_the_lab_reaction_when_the_steps_talk_about_it() -> None:
    assert ENTRY_CUES[2] == (CUE_MINE_ACQUIRES,)  # the bench the user got is theirs
    assert CUE_ALICE_ACQUIRES in ENTRY_CUES[4]
    assert CUE_SERIAL_ONLINE in ENTRY_CUES[5]
    assert ENTRY_CUES[6] == (CUE_MINE_QUEUED,)
    assert DELAYED_ENTRY_CUES[6] == ((1, CUE_MINE_ALLOCATED), (2, CUE_MINE_ACQUIRED))


def test_every_trigger_advances_by_at_most_one_step_from_any_position() -> None:
    for start in range(STEP_COUNT + 1):
        for trigger in (
            lambda c: c.skip(),
            lambda c: c.on_cursor_changed("bench-09"),
            lambda c: c.on_detail_close(),
            lambda c: c.on_copy(_entry("Manage", "Acquire")),
            lambda c: c.on_coordinator_switch("desk"),
        ):
            controller = _at(start)
            controller.on_cursor_changed("bench-01")
            trigger(controller)
            assert controller.step - start in (0, 1)
