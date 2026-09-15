"""Tests for ScriptedFleet: the tour's fake-data FleetSource.

The fleet delivers its initial state immediately and everything else only
when cued, so these tests never wait on wall-clock timing.
"""

import asyncio
from collections.abc import Callable

import pytest

from labgrid_tui.coordinator.models import ReservationState
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    PlaceChanged,
    ResourceChanged,
)
from labgrid_tui.model.identity import current_id
from labgrid_tui.tour.fleet import (
    CUE_ALICE_ACQUIRES,
    CUE_MINE_ACQUIRES,
    CUE_MINE_ALLOCATED,
    CUE_MINE_QUEUED,
    CUE_SERIAL_OFFLINE,
    CUE_SERIAL_ONLINE,
    DESK_SCRIPT,
    LAB_SCRIPT,
    SCRIPTS,
    ScriptedFleet,
    fleet_source_factory,
)


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


async def _run_until_live(fleet: ScriptedFleet) -> "tuple[list[Event], asyncio.Task[None]]":
    events: list[Event] = []
    task = asyncio.create_task(fleet.start(events.append))
    await _wait_for(
        lambda: any(isinstance(e, ConnectionChanged) and e.state is ConnState.LIVE for e in events)
    )
    return events, task


async def _stop(fleet: ScriptedFleet, task: "asyncio.Task[None]") -> None:
    fleet.stop()
    await asyncio.wait_for(task, timeout=1.0)


async def test_lab_initial_has_six_benches_with_expected_resources() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT)
    events, task = await _run_until_live(fleet)
    try:
        places = [e.place for e in events if isinstance(e, PlaceChanged)]
        assert [p.name for p in places] == [f"bench-0{i}" for i in range(1, 7)]
        resources = [e.resource for e in events if isinstance(e, ResourceChanged)]
        by_group = {r.group for r in resources}
        assert by_group == {f"bench-0{i}" for i in range(1, 6)}  # bench-06 has none
        assert all(r.avail for r in resources)
        assert all(p.acquired is None for p in places)
        assert {p.tags["board"] for p in places} == {"phyboard-a", "phyboard-b", "imx8", "rpi4"}
    finally:
        await _stop(fleet, task)


async def test_cues_emit_once_and_only_when_asked() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT)
    events, task = await _run_until_live(fleet)
    try:
        baseline = len(events)
        assert fleet.cue(CUE_ALICE_ACQUIRES) is True
        acquired = [
            e for e in events[baseline:] if isinstance(e, PlaceChanged) and e.place.acquired
        ]
        assert [(e.place.name, e.place.acquired) for e in acquired] == [
            ("bench-03", "laptop/alice")
        ]
        assert fleet.cue(CUE_ALICE_ACQUIRES) is False  # repeats are no-ops
        assert fleet.cue("no-such-cue") is False
        before = len(events)
        assert fleet.cue(CUE_SERIAL_OFFLINE) is True
        assert fleet.cue(CUE_SERIAL_ONLINE) is True
        serial = [e.resource for e in events[before:] if isinstance(e, ResourceChanged)]
        assert [(r.group, r.name, r.avail) for r in serial] == [
            ("bench-05", "serial", False),
            ("bench-05", "serial", True),
        ]
        assert fleet.cued == {CUE_ALICE_ACQUIRES, CUE_SERIAL_OFFLINE, CUE_SERIAL_ONLINE}
    finally:
        await _stop(fleet, task)


async def test_my_reservation_appears_when_queued_and_is_allocated_when_cued() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT)
    events, task = await _run_until_live(fleet)
    try:
        me = current_id()
        before = await fleet.get_reservations()
        assert [(r.owner, r.state) for r in before] == [
            ("laptop/alice", ReservationState.allocated)
        ]
        assert before[0].allocations == {"main": "bench-04"}
        assert fleet.cue(CUE_MINE_QUEUED) is True
        queued = await fleet.get_reservations()
        assert [(r.owner, r.state) for r in queued] == [
            ("laptop/alice", ReservationState.allocated),
            (me, ReservationState.waiting),
        ]
        assert fleet.cue(CUE_MINE_ALLOCATED) is True
        after = await fleet.get_reservations()
        assert [(r.owner, r.state) for r in after][1] == (me, ReservationState.allocated)
        assert after[1].allocations == {"main": "bench-03"}
        # alice handed bench-03 over: the place now carries my token.
        handed = [e for e in events if isinstance(e, PlaceChanged) and e.place.name == "bench-03"]
        assert handed[-1].place.acquired is None
        assert handed[-1].place.reservation == after[1].token
    finally:
        await _stop(fleet, task)


async def test_mine_acquires_cue_targets_the_bench_the_user_chose() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT)
    events, task = await _run_until_live(fleet)
    try:
        assert fleet.cue(CUE_MINE_ACQUIRES, place="bench-02") is True
        me = current_id()
        mine = [e for e in events if isinstance(e, PlaceChanged) and e.place.acquired == me]
        assert [e.place.name for e in mine] == ["bench-02"]
        assert fleet.cue(CUE_MINE_ACQUIRES, place="bench-01") is False  # a cue fires once
    finally:
        await _stop(fleet, task)


async def test_desk_script_has_two_places_and_no_cues() -> None:
    fleet = ScriptedFleet(DESK_SCRIPT)
    events, task = await _run_until_live(fleet)
    try:
        assert [e.place.name for e in events if isinstance(e, PlaceChanged)] == [
            "desk-01",
            "desk-02",
        ]
        assert fleet.cue(CUE_ALICE_ACQUIRES) is False
        assert await fleet.get_reservations() == []
    finally:
        await _stop(fleet, task)


async def test_stop_ends_the_run_with_no_leaked_tasks() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT)
    before = asyncio.all_tasks()
    task = asyncio.create_task(fleet.start(lambda _event: None))
    await asyncio.sleep(0.05)
    fleet.stop()
    await asyncio.wait_for(task, timeout=1.0)
    leaked = asyncio.all_tasks() - before - {asyncio.current_task()}
    assert not leaked
    assert task.done() and not task.cancelled()


def test_fleet_source_factory_maps_known_addresses() -> None:
    assert SCRIPTS["tour:lab"] is LAB_SCRIPT
    assert SCRIPTS["tour:desk"] is DESK_SCRIPT
    factory = fleet_source_factory()
    assert factory("tour:lab") is not factory("tour:lab")  # fresh per connect


@pytest.mark.parametrize("address", ["tour:lab", "tour:unknown"])
async def test_unknown_address_falls_back_to_lab_script(address: str) -> None:
    fleet = fleet_source_factory()(address)
    events, task = await _run_until_live(fleet)
    try:
        places = {e.place.name for e in events if isinstance(e, PlaceChanged)}
        assert places == {f"bench-0{i}" for i in range(1, 7)}
    finally:
        await _stop(fleet, task)
