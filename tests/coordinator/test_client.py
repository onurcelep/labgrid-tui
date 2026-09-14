import pytest

from labgrid_tui.coordinator.client import CoordinatorClient, CoordinatorError
from labgrid_tui.coordinator.models import ReservationState
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from tests.fake_coordinator import FakeCoordinator


async def test_get_places(fake_coordinator: tuple[FakeCoordinator, str]) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", comment="hi", tags={"board": "imx8"}))
    client = CoordinatorClient(address)
    try:
        places = await client.get_places()
        assert [p.name for p in places] == ["tb-1"]
        assert places[0].tags == {"board": "imx8"}
    finally:
        await client.close()


async def test_get_reservations(fake_coordinator: tuple[FakeCoordinator, str]) -> None:
    servicer, address = fake_coordinator
    servicer.reservations.append(pb2.Reservation(owner="h/u", token="TOK", state=0))
    client = CoordinatorClient(address)
    try:
        reservations = await client.get_reservations()
        assert reservations[0].token == "TOK"
        assert reservations[0].state is ReservationState.waiting
    finally:
        await client.close()


async def test_transport_error_becomes_coordinator_error(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.fail_unary = True
    client = CoordinatorClient(address)
    try:
        with pytest.raises(CoordinatorError):
            await client.get_places()
    finally:
        await client.close()


async def test_unreachable_coordinator() -> None:
    client = CoordinatorClient("127.0.0.1:1")  # nothing listens here
    try:
        with pytest.raises(CoordinatorError):
            await client.get_places(timeout=0.5)
    finally:
        await client.close()
