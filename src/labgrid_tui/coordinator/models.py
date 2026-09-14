"""Own dataclasses for coordinator wire messages.

Unknown resource classes/attributes are passed through untouched; unknown
reservation states map to `invalid`. This is the version-compatibility
strategy: render what the lab reports, never validate against a known list.
"""

import enum
from dataclasses import dataclass
from typing import Any

from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2

ResourceKey = tuple[str, str, str]


class ReservationState(enum.Enum):
    waiting = 0
    allocated = 1
    acquired = 2
    expired = 3
    invalid = 4

    @classmethod
    def from_wire(cls, value: int) -> "ReservationState":
        try:
            return cls(value)
        except ValueError:
            return cls.invalid


@dataclass(frozen=True)
class ResourceMatchPattern:
    exporter: str
    group: str
    cls: str
    name: str | None = None
    rename: str | None = None


@dataclass(frozen=True)
class Resource:
    exporter: str
    group: str
    name: str
    cls: str
    params: dict[str, Any]
    extra: dict[str, Any]
    acquired: str
    avail: bool


@dataclass(frozen=True)
class Place:
    name: str
    aliases: tuple[str, ...]
    comment: str
    tags: dict[str, str]
    matches: tuple[ResourceMatchPattern, ...]
    acquired: str | None
    acquired_resources: tuple[str, ...]
    allowed: tuple[str, ...]
    created: float
    changed: float
    reservation: str | None


@dataclass(frozen=True)
class Reservation:
    owner: str
    token: str
    state: ReservationState
    prio: float
    filters: dict[str, dict[str, str]]
    allocations: dict[str, str]
    created: float
    timeout: float


def resource_key(resource: Resource) -> ResourceKey:
    return (resource.exporter, resource.group, resource.name)


def _unwrap(value: pb2.MapValue) -> Any:
    kind = value.WhichOneof("kind")
    if kind is None:
        return None
    return getattr(value, kind)


def _match_from_pb2(pb: pb2.ResourceMatch) -> ResourceMatchPattern:
    return ResourceMatchPattern(
        exporter=pb.exporter,
        group=pb.group,
        cls=pb.cls,
        name=pb.name if pb.HasField("name") else None,
        rename=pb.rename if pb.HasField("rename") else None,
    )


def place_from_pb2(pb: pb2.Place) -> Place:
    return Place(
        name=pb.name,
        aliases=tuple(pb.aliases),
        comment=pb.comment,
        tags=dict(pb.tags),
        matches=tuple(_match_from_pb2(m) for m in pb.matches),
        acquired=pb.acquired if pb.HasField("acquired") else None,
        acquired_resources=tuple(pb.acquired_resources),
        allowed=tuple(pb.allowed),
        created=pb.created,
        changed=pb.changed,
        reservation=pb.reservation if pb.HasField("reservation") else None,
    )


def resource_from_pb2(pb: pb2.Resource) -> Resource:
    return Resource(
        exporter=pb.path.exporter_name,
        group=pb.path.group_name,
        name=pb.path.resource_name,
        cls=pb.cls,
        params={k: _unwrap(v) for k, v in pb.params.items()},
        extra={k: _unwrap(v) for k, v in pb.extra.items()},
        acquired=pb.acquired,
        avail=pb.avail,
    )


def reservation_from_pb2(pb: pb2.Reservation) -> Reservation:
    return Reservation(
        owner=pb.owner,
        token=pb.token,
        state=ReservationState.from_wire(pb.state),
        prio=pb.prio,
        filters={name: dict(f.filter) for name, f in pb.filters.items()},
        allocations=dict(pb.allocations),
        created=pb.created,
        timeout=pb.timeout,
    )
