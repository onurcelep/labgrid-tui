"""ScriptedFleet: a deterministic, in-memory FleetSource for the tour.

Delivers a fixed initial fleet on the running event loop instead of talking
to gRPC, then emits further coordinator events only when the tour cues them
(`cue()`), so each event lands at the moment the tour is talking about it
rather than at some wall-clock offset the user may or may not be looking at.
No network, identical output every run.
"""

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, replace
from typing import Any

from labgrid_tui.coordinator.models import (
    Place,
    Reservation,
    ReservationState,
    Resource,
    ResourceMatchPattern,
)
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    PlaceChanged,
    ResourceChanged,
)
from labgrid_tui.model.identity import current_id

EXPORTER = "rack-1"

# name, board, env, site, comment. bench-06 gets no resources (see
# _lab_initial): a place with nothing exported yet is a real, common state
# worth showing in the tour.
_BENCH_META: tuple[tuple[str, str, str, str, str], ...] = (
    ("bench-01", "am62x", "ci", "lab1", "CI regression bench"),
    ("bench-02", "stm32mp1", "ci", "lab1", "CI regression bench"),
    ("bench-03", "imx8", "dev", "lab1", "Dev bring-up bench"),
    ("bench-04", "imx8", "dev", "lab2", "Dev bring-up bench"),
    ("bench-05", "rpi4", "staging", "lab2", "Staging validation bench"),
    ("bench-06", "rpi4", "staging", "lab2", "Spare, no resources wired up yet"),
)

# A couple of add-alias names, so the tour shows the "name (alias)" form
# the table and labgrid-client's places listing both use.
_BENCH_ALIASES: dict[str, tuple[str, ...]] = {
    "bench-01": ("smoke",),
    "bench-03": ("bringup",),
}

# bench-04 is reserved by alice for the whole tour (a black dot from the
# start). My own reservation appears only when the tour has the user queue
# for alice's bench-03; it is allocated when alice lets go, and the bench
# is then acquired for me, exactly what the copied one-line command does.
_MY_TOKEN = "tour-mine-1"
_ALICE_TOKEN = "tour-alice-1"
_ALICE = "laptop/alice"

# Cue names the tour can fire, in the order the tour uses them.
CUE_MINE_ACQUIRES = "mine_acquires"  # takes place=<name>: the bench the user chose
CUE_ALICE_ACQUIRES = "alice_acquires"
CUE_SERIAL_OFFLINE = "serial_offline"
CUE_SERIAL_ONLINE = "serial_online"
CUE_MINE_QUEUED = "mine_queued"
CUE_MINE_ALLOCATED = "mine_allocated"
CUE_MINE_ACQUIRED = "mine_acquired"  # the queued one-liner completes: bench-03 is mine


def _place(
    name: str,
    *,
    tags: dict[str, str],
    comment: str,
    aliases: tuple[str, ...] = (),
    acquired: str | None = None,
    reservation: str | None = None,
) -> Place:
    return Place(
        name=name,
        aliases=aliases,
        comment=comment,
        tags=tags,
        matches=(ResourceMatchPattern(exporter=EXPORTER, group=name, cls="*"),),
        acquired=acquired,
        acquired_resources=(),
        allowed=(),
        created=0.0,
        changed=0.0,
        reservation=reservation,
    )


def _resource(
    group: str, name: str, cls: str, params: dict[str, Any], *, avail: bool = True
) -> Resource:
    return Resource(
        exporter=EXPORTER,
        group=group,
        name=name,
        cls=cls,
        params=params,
        extra={},
        acquired="",
        avail=avail,
    )


def _bench_resources(name: str, index: int) -> list[Resource]:
    return [
        _resource(name, "power", "NetworkPowerPort", {"host": EXPORTER, "index": index}),
        _resource(name, "serial", "NetworkSerialPort", {"host": EXPORTER, "port": 4000 + index}),
        _resource(
            name, "net", "NetworkService", {"address": f"10.0.{index}.2", "username": "root"}
        ),
    ]


def _bench_place(name: str) -> Place:
    _, board, env, site, comment = next(m for m in _BENCH_META if m[0] == name)
    reservation = _ALICE_TOKEN if name == "bench-04" else None
    return _place(
        name,
        tags={"board": board, "env": env, "site": site},
        comment=comment,
        aliases=_BENCH_ALIASES.get(name, ()),
        reservation=reservation,
    )


def _lab_initial() -> list[tuple[Place, list[Resource]]]:
    result: list[tuple[Place, list[Resource]]] = []
    for index, (name, *_rest) in enumerate(_BENCH_META, start=1):
        place = _bench_place(name)
        resources = [] if name == "bench-06" else _bench_resources(name, index)
        result.append((place, resources))
    return result


def _mine_acquires(place: str = "bench-01", **_ignored: str) -> list[Event]:
    """The bench the user chose at the acquire step becomes theirs."""
    if not any(m[0] == place for m in _BENCH_META):
        place = "bench-01"
    return [PlaceChanged(replace(_bench_place(place), acquired=current_id()))]


def _alice_acquires_bench_03(**_ignored: str) -> list[Event]:
    place = replace(_bench_place("bench-03"), acquired=_ALICE)
    return [PlaceChanged(place)]


def _alice_hands_over_bench_03(**_ignored: str) -> list[Event]:
    """Alice releases bench-03 and the coordinator allocates it to my
    queued reservation: the place now carries my token."""
    place = replace(_bench_place("bench-03"), acquired=None, reservation=_MY_TOKEN)
    return [PlaceChanged(place)]


def _mine_acquires_bench_03(**_ignored: str) -> list[Event]:
    """The reserve-and-acquire line finishes: bench-03 is acquired by me
    under my reservation token."""
    place = replace(_bench_place("bench-03"), acquired=current_id(), reservation=_MY_TOKEN)
    return [PlaceChanged(place)]


def _bench_05_serial(*, avail: bool, **_ignored: str) -> list[Event]:
    resource = _resource(
        "bench-05", "serial", "NetworkSerialPort", {"host": EXPORTER, "port": 4005}, avail=avail
    )
    return [ResourceChanged(resource)]


def _reservation(
    owner: str, token: str, state: ReservationState, allocated_place: str | None
) -> Reservation:
    allocations = {"main": allocated_place} if allocated_place else {}
    return Reservation(
        owner=owner,
        token=token,
        state=state,
        prio=0.0,
        filters={},
        allocations=allocations,
        created=0.0,
        timeout=0.0,
    )


def _lab_reservations(cued: frozenset[str]) -> list[Reservation]:
    """alice's allocation on bench-04 stands for the whole tour. My own
    reservation exists once the user has queued for bench-03 (waiting),
    is allocated once alice hands the bench over, and is acquired once the
    one-liner has taken the bench."""
    reservations = [_reservation(_ALICE, _ALICE_TOKEN, ReservationState.allocated, "bench-04")]
    if CUE_MINE_QUEUED in cued:
        if CUE_MINE_ACQUIRED in cued:
            state = ReservationState.acquired
        elif CUE_MINE_ALLOCATED in cued:
            state = ReservationState.allocated
        else:
            state = ReservationState.waiting
        allocated = "bench-03" if state is not ReservationState.waiting else None
        reservations.append(_reservation(current_id(), _MY_TOKEN, state, allocated))
    return reservations


def _desk_initial() -> list[tuple[Place, list[Resource]]]:
    return [
        (
            _place(
                "desk-01",
                tags={"board": "desk-a", "env": "dev", "site": "desk"},
                comment="Desk bench",
            ),
            [
                _resource(
                    "desk-01", "net", "NetworkService", {"address": "10.0.9.2", "username": "root"}
                )
            ],
        ),
        (
            _place(
                "desk-02",
                tags={"board": "desk-b", "env": "dev", "site": "desk"},
                comment="Desk bench",
            ),
            [_resource("desk-02", "power", "NetworkPowerPort", {"host": EXPORTER, "index": 1})],
        ),
    ]


CueBuilder = Callable[..., list[Event]]


@dataclass(frozen=True)
class FleetScript:
    initial: tuple[tuple[Place, tuple[Resource, ...]], ...]
    cues: dict[str, CueBuilder]
    reservations: Callable[[frozenset[str]], list[Reservation]]


def _script(
    initial: list[tuple[Place, list[Resource]]],
    cues: dict[str, CueBuilder],
    reservations: Callable[[frozenset[str]], list[Reservation]],
) -> FleetScript:
    return FleetScript(
        initial=tuple((place, tuple(resources)) for place, resources in initial),
        cues=cues,
        reservations=reservations,
    )


LAB_SCRIPT = _script(
    _lab_initial(),
    {
        CUE_MINE_ACQUIRES: _mine_acquires,
        CUE_ALICE_ACQUIRES: _alice_acquires_bench_03,
        CUE_SERIAL_OFFLINE: lambda **_kw: _bench_05_serial(avail=False),
        CUE_SERIAL_ONLINE: lambda **_kw: _bench_05_serial(avail=True),
        CUE_MINE_QUEUED: lambda **_kw: [],  # observable through get_reservations()
        CUE_MINE_ALLOCATED: _alice_hands_over_bench_03,
        CUE_MINE_ACQUIRED: _mine_acquires_bench_03,
    },
    _lab_reservations,
)

DESK_SCRIPT = _script(_desk_initial(), {}, lambda _cued: [])

SCRIPTS: dict[str, FleetScript] = {
    "tour:lab": LAB_SCRIPT,
    "tour:desk": DESK_SCRIPT,
}


class ScriptedFleet:
    """FleetSource that delivers *script* instead of talking to a coordinator."""

    def __init__(self, script: FleetScript) -> None:
        self._script = script
        self._stopped = asyncio.Event()
        self._on_event: Callable[[Event], None] | None = None
        self._cued: set[str] = set()

    def start(self, on_event: Callable[[Event], None]) -> Coroutine[Any, Any, None]:
        self._on_event = on_event
        return self._run(on_event)

    async def _run(self, on_event: Callable[[Event], None]) -> None:
        on_event(ConnectionChanged(ConnState.CONNECTING))
        for place, resources in self._script.initial:
            on_event(PlaceChanged(place))
            for resource in resources:
                on_event(ResourceChanged(resource))
        on_event(ConnectionChanged(ConnState.LIVE))
        # Idle until stop() (or the worker running this coroutine is
        # cancelled), mirroring EventStream.run's run-until-stopped shape so a
        # coordinator switch tears both fleet sources down the same way.
        await self._stopped.wait()

    def cue(self, name: str, **params: str) -> bool:
        """Emit the events for *name* now. Unknown cues and repeats are
        no-ops; returns whether the cue fired. *params* reach the cue's
        builder (the acquired bench's name, for instance)."""
        build = self._script.cues.get(name)
        if build is None or name in self._cued or self._on_event is None:
            return False
        self._cued.add(name)
        for event in build(**params):
            self._on_event(event)
        return True

    @property
    def cued(self) -> frozenset[str]:
        return frozenset(self._cued)

    def stop(self) -> None:
        self._stopped.set()

    def retry_now(self) -> None:
        pass  # the tour never disconnects; nothing to retry

    async def get_reservations(self) -> list[Reservation]:
        return self._script.reservations(self.cued)

    async def aclose(self) -> None:
        self.stop()


def fleet_source_factory() -> Callable[[str], ScriptedFleet]:
    """A fresh ScriptedFleet per connect/reconnect, keyed by the tour's
    "tour:lab"/"tour:desk" addresses (see labgrid_tui.tour.app)."""

    def factory(address: str) -> ScriptedFleet:
        return ScriptedFleet(SCRIPTS.get(address, LAB_SCRIPT))

    return factory
