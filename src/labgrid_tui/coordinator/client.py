"""Read-only unary client. GetPlaces and GetReservations only.

PollReservation is deliberately absent: the coordinator refreshes the
reservation timeout on every poll, making it a keep-alive with write side
effects, not a read.
"""

from typing import Any

import grpc
import grpc.aio

from labgrid_tui.coordinator.models import (
    Place,
    Reservation,
    place_from_pb2,
    reservation_from_pb2,
)
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2_grpc as pb2_grpc

# Upstream labgrid-client's channel options, verbatim. The ping_timeout_ms
# override works around a grpc regression where its 60s default supersedes
# keepalive_timeout_ms; without these, dead connections take minutes to notice.
CHANNEL_OPTIONS: list[tuple[str, int]] = [
    ("grpc.keepalive_time_ms", 7500),
    ("grpc.keepalive_timeout_ms", 10000),
    ("grpc.http2.ping_timeout_ms", 10000),
    ("grpc.http2.max_pings_without_data", 0),
]


class CoordinatorError(Exception):
    """Any transport-level failure talking to the coordinator."""


class CoordinatorClient:
    def __init__(self, address: str) -> None:
        self.address = address
        self.channel: grpc.aio.Channel = grpc.aio.insecure_channel(address, options=CHANNEL_OPTIONS)
        # Generated pb2_grpc has no type stubs (no .pyi shipped), so its
        # __init__ is untyped from mypy's perspective.
        self._stub = pb2_grpc.CoordinatorStub(self.channel)  # type: ignore[no-untyped-call]

    async def get_places(self, timeout: float = 5.0) -> list[Place]:
        response = await self._call(
            self._stub.GetPlaces, pb2.GetPlacesRequest(), timeout, "GetPlaces"
        )
        return [place_from_pb2(p) for p in response.places]

    async def get_reservations(self, timeout: float = 5.0) -> list[Reservation]:
        response = await self._call(
            self._stub.GetReservations, pb2.GetReservationsRequest(), timeout, "GetReservations"
        )
        return [reservation_from_pb2(r) for r in response.reservations]

    async def close(self) -> None:
        await self.channel.close(grace=None)

    @staticmethod
    async def _call(method: Any, request: Any, timeout: float, name: str) -> Any:
        try:
            return await method(request, timeout=timeout)
        except grpc.aio.AioRpcError as exc:
            raise CoordinatorError(f"{name} failed: {exc.details()}") from exc
