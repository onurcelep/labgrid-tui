from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2_grpc as pb2_grpc


def test_place_message_roundtrip() -> None:
    place = pb2.Place(name="tb-1", comment="hello", tags={"board": "imx8"})
    data = place.SerializeToString()
    parsed = pb2.Place.FromString(data)
    assert parsed.name == "tb-1"
    assert parsed.tags["board"] == "imx8"


def test_grpc_stub_exists() -> None:
    assert hasattr(pb2_grpc, "CoordinatorStub")
    assert hasattr(pb2_grpc, "add_CoordinatorServicer_to_server")
