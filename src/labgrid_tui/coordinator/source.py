"""FleetSource seam: how LabgridTuiApp obtains coordinator events.

LabgridTuiApp talks to whatever ``fleet_source_factory`` produces for a
given address, never to CoordinatorClient/EventStream directly.
GrpcFleetSource (the default) wraps that pair unchanged; the tour's
ScriptedFleet (labgrid_tui.tour.fleet) implements the same seam against a
deterministic in-memory script instead of a network connection.
"""

from collections.abc import Callable, Coroutine
from typing import Any, Protocol

import labgrid_tui
from labgrid_tui.coordinator.client import CoordinatorClient
from labgrid_tui.coordinator.models import Reservation
from labgrid_tui.coordinator.stream import Event, EventStream
from labgrid_tui.model.identity import current_id


class FleetSource(Protocol):
    """What LabgridTuiApp needs to show a live fleet: an event feed plus
    the one poll RPC (reservations aren't pushed over the event stream)."""

    def start(self, on_event: Callable[[Event], None]) -> Coroutine[Any, Any, None]:
        """Return the coroutine to run as a background worker until stop()."""
        ...

    def stop(self) -> None:
        """Signal the running start() coroutine to wind down."""
        ...

    def retry_now(self) -> None:
        """Cut short a pending reconnect backoff, if any."""
        ...

    async def get_reservations(self) -> list[Reservation]: ...

    async def aclose(self) -> None:
        """Release any held resources (connections, tasks)."""
        ...


class GrpcFleetSource:
    """Default FleetSource: the unchanged CoordinatorClient + EventStream pair."""

    def __init__(self, address: str) -> None:
        self.client = CoordinatorClient(address)
        self.stream: EventStream | None = None

    def start(self, on_event: Callable[[Event], None]) -> Coroutine[Any, Any, None]:
        # Deferred to start() rather than __init__: EventStream needs the
        # on_event callback, which the app only hands over once it starts
        # the worker running this coroutine.
        self.stream = EventStream(
            self.client.channel,
            on_event,
            client_name=current_id(),
            version=labgrid_tui.__version__,
        )
        return self.stream.run()

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()

    def retry_now(self) -> None:
        if self.stream is not None:
            self.stream.retry_now()

    async def get_reservations(self) -> list[Reservation]:
        return await self.client.get_reservations()

    async def aclose(self) -> None:
        await self.client.close()
