"""ScriptedFleet: a deterministic, in-memory FleetSource for the tour.

Replays a fixed sequence of coordinator events on the running event loop
instead of talking to gRPC, so the tour has no network dependency and
produces identical output every run. Event timing is scaled by *speed* so
tests can run the whole script in well under a second.
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
    ("bench-01", "phyboard-a", "ci", "lab1", "CI regression bench"),
    ("bench-02", "phyboard-b", "ci", "lab1", "CI regression bench"),
    ("bench-03", "imx8", "dev", "lab1", "Dev bring-up bench"),
    ("bench-04", "imx8", "dev", "lab2", "Dev bring-up bench"),
    ("bench-05", "rpi4", "staging", "lab2", "Staging validation bench"),
    ("bench-06", "rpi4", "staging", "lab2", "Spare, no resources wired up yet"),
)

# Reservation tokens, fixed so Place.reservation and Reservation.token agree
# from the very first frame: "mine" is present (waiting) from t=0, alice's
# only appears once its own scripted offset is reached (see _lab_reservations_at).
_MY_TOKEN = "tour-mine-1"
_ALICE_TOKEN = "tour-alice-1"
_ALICE = "laptop/alice"


def _place(
    name: str,
    *,
    tags: dict[str, str],
    comment: str,
    acquired: str | None = None,
    reservation: str | None = None,
) -> Place:
    return Place(
        name=name,
        aliases=(),
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
    reservation = {"bench-02": _MY_TOKEN, "bench-04": _ALICE_TOKEN}.get(name)
    return _place(
        name,
        tags={"board": board, "env": env, "site": site},
        comment=comment,
        reservation=reservation,
    )


def _lab_initial() -> list[tuple[Place, list[Resource]]]:
    result: list[tuple[Place, list[Resource]]] = []
    for index, (name, *_rest) in enumerate(_BENCH_META, start=1):
        place = _bench_place(name)
        resources = [] if name == "bench-06" else _bench_resources(name, index)
        result.append((place, resources))
    return result


def _alice_acquires_bench_03() -> list[Event]:
    place = replace(_bench_place("bench-03"), acquired=_ALICE)
    return [PlaceChanged(place)]


def _bench_05_serial(*, avail: bool) -> list[Event]:
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


def _lab_reservations_at(elapsed: float) -> list[Reservation]:
    me = current_id()
    mine_state = ReservationState.allocated if elapsed >= 10.0 else ReservationState.waiting
    reservations = [
        _reservation(
            me,
            _MY_TOKEN,
            mine_state,
            "bench-02" if mine_state is ReservationState.allocated else None,
        )
    ]
    if elapsed >= 8.0:
        alice_state = ReservationState.allocated if elapsed >= 12.0 else ReservationState.waiting
        reservations.append(
            _reservation(
                _ALICE,
                _ALICE_TOKEN,
                alice_state,
                "bench-04" if alice_state is ReservationState.allocated else None,
            )
        )
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


@dataclass(frozen=True)
class FleetScript:
    initial: tuple[tuple[Place, tuple[Resource, ...]], ...]
    # (offset in unscaled seconds, events to emit), ascending offset order.
    timeline: tuple[tuple[float, Callable[[], list[Event]]], ...]
    reservations_at: Callable[[float], list[Reservation]]


def _script(
    initial: list[tuple[Place, list[Resource]]],
    timeline: list[tuple[float, Callable[[], list[Event]]]],
    reservations_at: Callable[[float], list[Reservation]],
) -> FleetScript:
    return FleetScript(
        initial=tuple((place, tuple(resources)) for place, resources in initial),
        timeline=tuple(timeline),
        reservations_at=reservations_at,
    )


LAB_SCRIPT = _script(
    _lab_initial(),
    [
        (4.0, _alice_acquires_bench_03),
        (16.0, lambda: _bench_05_serial(avail=False)),
        (22.0, lambda: _bench_05_serial(avail=True)),
    ],
    _lab_reservations_at,
)

DESK_SCRIPT = _script(_desk_initial(), [], lambda _elapsed: [])

SCRIPTS: dict[str, FleetScript] = {
    "tour:lab": LAB_SCRIPT,
    "tour:desk": DESK_SCRIPT,
}


class ScriptedFleet:
    """FleetSource that replays *script* instead of talking to a coordinator."""

    def __init__(self, script: FleetScript, speed: float) -> None:
        self._script = script
        # The CLI (--speed, see __main__.py's _positive_float) already
        # rejects a non-positive value; this is only a backstop against a
        # non-positive speed passed by an embedder, not a documented way to
        # get the default.
        self._speed = speed if speed > 0 else 1.0
        self._stopped = asyncio.Event()
        self._start: float | None = None

    def start(self, on_event: Callable[[Event], None]) -> Coroutine[Any, Any, None]:
        return self._run(on_event)

    async def _run(self, on_event: Callable[[Event], None]) -> None:
        self._start = asyncio.get_running_loop().time()
        on_event(ConnectionChanged(ConnState.CONNECTING))
        for place, resources in self._script.initial:
            on_event(PlaceChanged(place))
            for resource in resources:
                on_event(ResourceChanged(resource))
        on_event(ConnectionChanged(ConnState.LIVE))

        previous_offset = 0.0
        for offset, build_events in self._script.timeline:
            if await self._wait((offset - previous_offset) / self._speed):
                return
            previous_offset = offset
            for event in build_events():
                on_event(event)
        # Nothing left to replay; idle until stop() (or the worker running
        # this coroutine is cancelled directly), mirroring EventStream.run's
        # run-until-stopped shape so a coordinator switch tears it down the
        # same way for both fleet sources.
        await self._stopped.wait()

    async def _wait(self, delay: float) -> bool:
        """Wait up to *delay* seconds, or until stop(). Returns True if
        stop() cut the wait short."""
        try:
            await asyncio.wait_for(self._stopped.wait(), timeout=max(delay, 0.0))
        except TimeoutError:
            return False
        return True

    def stop(self) -> None:
        self._stopped.set()

    def retry_now(self) -> None:
        pass  # the tour never disconnects; nothing to retry

    async def get_reservations(self) -> list[Reservation]:
        elapsed = 0.0
        if self._start is not None:
            elapsed = (asyncio.get_running_loop().time() - self._start) * self._speed
        return self._script.reservations_at(elapsed)

    async def aclose(self) -> None:
        self.stop()


def fleet_source_factory(speed: float) -> Callable[[str], ScriptedFleet]:
    """A fresh ScriptedFleet per connect/reconnect, keyed by the tour's
    "tour:lab"/"tour:desk" addresses (see labgrid_tui.tour.app)."""

    def factory(address: str) -> ScriptedFleet:
        return ScriptedFleet(SCRIPTS.get(address, LAB_SCRIPT), speed)

    return factory
