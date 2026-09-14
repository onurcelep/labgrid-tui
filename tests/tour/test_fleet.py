"""Tests for ScriptedFleet: the tour's fake-data FleetSource.

Every offset in the script is scaled by a large ``speed`` factor so these
tests observe the whole timeline (4s..22s of "design time") in well under a
second of wall-clock time, polling instead of sleeping fixed amounts so
scheduler jitter can't make them flaky.
"""

import asyncio
from collections.abc import Callable

import pytest

from labgrid_tui.coordinator.models import Reservation, ReservationState
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    PlaceChanged,
    ResourceChanged,
)
from labgrid_tui.model.identity import current_id
from labgrid_tui.tour.fleet import (
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


async def _wait_for_reservations(
    fleet: ScriptedFleet, predicate: Callable[[list[Reservation]], bool], timeout: float = 2.0
) -> list[Reservation]:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        reservations = await fleet.get_reservations()
        if predicate(reservations):
            return reservations
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("reservations never reached the expected state")
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


async def test_lab_initial_replay_has_six_benches_with_expected_resources() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT, speed=200.0)
    events, task = await _run_until_live(fleet)
    try:
        assert isinstance(events[0], ConnectionChanged)
        assert events[0].state is ConnState.CONNECTING

        places = [e.place for e in events if isinstance(e, PlaceChanged)]
        assert {p.name for p in places} == {f"bench-0{i}" for i in range(1, 7)}

        by_group: dict[str, list[str]] = {}
        for event in events:
            if isinstance(event, ResourceChanged):
                by_group.setdefault(event.resource.group, []).append(event.resource.cls)
        for i in range(1, 6):
            assert sorted(by_group[f"bench-0{i}"]) == sorted(
                ["NetworkPowerPort", "NetworkSerialPort", "NetworkService"]
            )
        assert "bench-06" not in by_group  # no resources wired up yet
    finally:
        await _stop(fleet, task)


async def test_alice_acquires_bench_03_at_scaled_offset() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT, speed=200.0)  # design +4s -> 0.02s real
    events, task = await _run_until_live(fleet)
    try:
        await _wait_for(
            lambda: any(
                isinstance(e, PlaceChanged)
                and e.place.name == "bench-03"
                and e.place.acquired == "laptop/alice"
                for e in events
            )
        )
    finally:
        await _stop(fleet, task)


async def test_bench_05_serial_goes_offline_then_online() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT, speed=300.0)  # design +22s -> ~0.073s real
    events, task = await _run_until_live(fleet)
    try:

        def _bench_05_serial_avail(avail: bool, since: int = 0) -> bool:
            return any(
                isinstance(e, ResourceChanged)
                and e.resource.group == "bench-05"
                and e.resource.name == "serial"
                and e.resource.avail is avail
                for e in events[since:]
            )

        await _wait_for(lambda: _bench_05_serial_avail(False))
        offline_index = len(events)
        # The initial replay already sent an online ResourceChanged (avail
        # starts True); only a *second* one after the offline event proves
        # the scripted "back online" transition actually fired.
        await _wait_for(lambda: _bench_05_serial_avail(True, since=offline_index))
    finally:
        await _stop(fleet, task)


async def test_get_reservations_reflects_a_realistic_queue_on_bench_04() -> None:
    """alice is allocated first; mine queues behind her, waiting; once she
    releases it (dropped from the list entirely, not left in some
    "released" state), mine is promoted to allocated. Neither bench-01 nor
    bench-02, the two benches the tour ever tells the user to acquire,
    appears in this queue at all."""
    fleet = ScriptedFleet(LAB_SCRIPT, speed=100.0)
    task = asyncio.create_task(fleet.start(lambda _event: None))
    try:
        me = current_id()

        reservations = await fleet.get_reservations()
        by_owner = {r.owner: r for r in reservations}
        assert by_owner[me].state is ReservationState.waiting
        assert by_owner[me].allocations == {}
        assert "laptop/alice" not in by_owner

        reservations = await _wait_for_reservations(
            fleet, lambda rs: any(r.owner == "laptop/alice" for r in rs)
        )
        by_owner = {r.owner: r for r in reservations}
        assert by_owner["laptop/alice"].state is ReservationState.allocated
        assert by_owner["laptop/alice"].allocations["main"] == "bench-04"
        assert by_owner[me].state is ReservationState.waiting  # still queued behind alice

        reservations = await _wait_for_reservations(
            fleet, lambda rs: "laptop/alice" not in {r.owner for r in rs}
        )
        by_owner = {r.owner: r for r in reservations}
        assert by_owner[me].state is ReservationState.allocated
        assert by_owner[me].allocations["main"] == "bench-04"
    finally:
        fleet.stop()
        await asyncio.wait_for(task, timeout=1.0)


async def test_desk_script_has_two_places() -> None:
    fleet = ScriptedFleet(DESK_SCRIPT, speed=100.0)
    events, task = await _run_until_live(fleet)
    try:
        places = {e.place.name for e in events if isinstance(e, PlaceChanged)}
        assert places == {"desk-01", "desk-02"}
    finally:
        await _stop(fleet, task)


async def test_stop_cancels_cleanly_with_no_leaked_tasks() -> None:
    fleet = ScriptedFleet(LAB_SCRIPT, speed=1.0)
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

    factory = fleet_source_factory(5.0)
    first = factory("tour:lab")
    second = factory("tour:lab")
    assert first is not second  # a fresh instance every connect/reconnect


@pytest.mark.parametrize("address", ["tour:lab", "tour:unknown"])
async def test_unknown_address_falls_back_to_lab_script(address: str) -> None:
    fleet = fleet_source_factory(200.0)(address)
    events, task = await _run_until_live(fleet)
    try:
        places = {e.place.name for e in events if isinstance(e, PlaceChanged)}
        assert places == {f"bench-0{i}" for i in range(1, 7)}
    finally:
        await _stop(fleet, task)
