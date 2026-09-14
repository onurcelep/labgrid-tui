from labgrid_tui.coordinator.models import (
    ReservationState,
    place_from_pb2,
    reservation_from_pb2,
    resource_from_pb2,
    resource_key,
)
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2


def _map_value(**kwargs: object) -> pb2.MapValue:
    v = pb2.MapValue()
    for key, val in kwargs.items():
        setattr(v, key, val)
    return v


def test_place_mapping_full() -> None:
    pb = pb2.Place(
        name="tb-1",
        aliases=["one"],
        comment="bench 1",
        tags={"board": "imx8"},
        acquired="host1/alice",
        acquired_resources=["exp1/g1/NetworkSerialPort/console"],
        allowed=["host2/bob"],
        created=1.0,
        changed=2.0,
        reservation="TOK123",
    )
    pb.matches.add(exporter="exp1", group="g1", cls="*")
    place = place_from_pb2(pb)
    assert place.name == "tb-1"
    assert place.acquired == "host1/alice"
    assert place.allowed == ("host2/bob",)
    assert place.reservation == "TOK123"
    assert place.matches[0].cls == "*"
    assert place.matches[0].name is None  # optional field unset -> None


def test_place_mapping_unacquired() -> None:
    place = place_from_pb2(pb2.Place(name="tb-2"))
    assert place.acquired is None
    assert place.reservation is None
    assert place.tags == {}


def test_resource_mapping_with_unknown_params() -> None:
    pb = pb2.Resource(cls="FutureResource", acquired="", avail=True)
    pb.path.exporter_name = "exp1"
    pb.path.group_name = "g1"
    pb.path.resource_name = "widget"
    pb.params["host"].string_value = "exp1.lab"
    pb.params["port"].int_value = 5555
    pb.params["invert"].bool_value = True  # attribute unknown to us: passes through
    pb.params["scale"].float_value = 1.5
    res = resource_from_pb2(pb)
    assert res.cls == "FutureResource"
    assert res.params == {"host": "exp1.lab", "port": 5555, "invert": True, "scale": 1.5}
    assert res.avail is True
    assert resource_key(res) == ("exp1", "g1", "widget")


def test_reservation_mapping_and_unknown_state() -> None:
    pb = pb2.Reservation(owner="host1/alice", token="TOK", state=1, prio=0.0)
    pb.filters["main"].filter["board"] = "imx8"
    pb.allocations["main"] = "tb-1"
    res = reservation_from_pb2(pb)
    assert res.state is ReservationState.allocated
    assert res.filters == {"main": {"board": "imx8"}}
    assert res.allocations == {"main": "tb-1"}
    assert ReservationState.from_wire(99) is ReservationState.invalid
