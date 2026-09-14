"""ClientStream session: handshake, event translation, reconnect.

Handshake order (startup, subscribe, subscribe, sync) is mandatory: the
coordinator creates its per-client session object on startup and a subscribe
sent first kills the stream server-side. CONNECTING is emitted before every
attempt so consumers clear state; the subscribe replay IS the snapshot; LIVE
only after the sync ack (replay complete).
"""

import asyncio
import contextlib
import enum
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import grpc
import grpc.aio

from labgrid_tui.coordinator.models import Place, Resource, place_from_pb2, resource_from_pb2
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)

_SYNC_ID = 1
_BACKOFF_START = 1.0
_BACKOFF_CAP = 30.0


class ConnState(enum.Enum):
    CONNECTING = enum.auto()
    LIVE = enum.auto()
    DISCONNECTED = enum.auto()


@dataclass(frozen=True)
class ConnectionChanged:
    state: ConnState


@dataclass(frozen=True)
class PlaceChanged:
    place: Place


@dataclass(frozen=True)
class PlaceDeleted:
    name: str


@dataclass(frozen=True)
class ResourceChanged:
    resource: Resource


@dataclass(frozen=True)
class ResourceDeleted:
    exporter: str
    group: str
    name: str


@dataclass(frozen=True)
class RetryScheduled:
    delay: float
    attempt: int


Event = (
    ConnectionChanged
    | PlaceChanged
    | PlaceDeleted
    | ResourceChanged
    | ResourceDeleted
    | RetryScheduled
)


async def _queue_iter(
    queue: "asyncio.Queue[pb2.ClientInMessage | None]",
) -> AsyncIterator[pb2.ClientInMessage]:
    while True:
        item = await queue.get()
        if item is None:
            return
        yield item


class EventStream:
    def __init__(
        self,
        channel: grpc.aio.Channel,
        on_event: Callable[[Event], None],
        client_name: str,
        version: str,
    ) -> None:
        self._stub = pb2_grpc.CoordinatorStub(channel)  # type: ignore[no-untyped-call]
        self._on_event = on_event
        self._client_name = client_name
        self._version = version
        self._stopped = asyncio.Event()
        self._backoff = _BACKOFF_START
        self._attempt = 0
        # Set by retry_now() to cut a pending backoff sleep short; cleared at
        # the start of each wait so a stale retry_now() from a previous cycle
        # can't pre-empt the next one.
        self._retry_requested = asyncio.Event()

    def stop(self) -> None:
        self._stopped.set()
        self._retry_requested.set()

    def retry_now(self) -> None:
        """Cancel a pending backoff sleep and reconnect immediately."""
        self._retry_requested.set()

    async def run(self) -> None:
        while not self._stopped.is_set():
            self._on_event(ConnectionChanged(ConnState.CONNECTING))
            try:
                await self._session()
            except grpc.aio.AioRpcError as exc:
                logger.debug("stream ended: %s", exc.details())
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("unexpected stream error")
            if self._stopped.is_set():
                return
            self._on_event(ConnectionChanged(ConnState.DISCONNECTED))
            self._attempt += 1
            self._on_event(RetryScheduled(delay=self._backoff, attempt=self._attempt))
            cut_short = await self._wait_backoff(self._backoff)
            if not cut_short:
                # Only a wait that actually ran its full course reflects a
                # coordinator that is still unreachable; a manual retry
                # says nothing about that and must not make the next
                # automatic attempt wait longer.
                self._backoff = min(self._backoff * 2, _BACKOFF_CAP)

    async def _wait_backoff(self, delay: float) -> bool:
        """Wait up to *delay* seconds, or until retry_now() cuts it short.

        Returns True if retry_now() interrupted the wait, False if the
        full delay elapsed on its own.
        """
        self._retry_requested.clear()
        try:
            await asyncio.wait_for(self._retry_requested.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    async def _session(self) -> None:
        out: asyncio.Queue[pb2.ClientInMessage | None] = asyncio.Queue()
        startup = pb2.ClientInMessage()
        startup.startup.version = self._version
        startup.startup.name = self._client_name
        sub_places = pb2.ClientInMessage()
        sub_places.subscribe.all_places = True
        sub_resources = pb2.ClientInMessage()
        sub_resources.subscribe.all_resources = True
        sync = pb2.ClientInMessage()
        sync.sync.id = _SYNC_ID
        for message in (startup, sub_places, sub_resources, sync):
            out.put_nowait(message)

        call = self._stub.ClientStream(_queue_iter(out))
        try:
            async for out_msg in call:
                for update in out_msg.updates:
                    self._dispatch(update)
                if out_msg.HasField("sync") and out_msg.sync.id == _SYNC_ID:
                    self._backoff = _BACKOFF_START
                    self._attempt = 0
                    self._on_event(ConnectionChanged(ConnState.LIVE))
        finally:
            out.put_nowait(None)
            with contextlib.suppress(Exception):
                call.cancel()

    def _dispatch(self, update: pb2.UpdateResponse) -> None:
        kind = update.WhichOneof("kind")
        if kind == "place":
            self._on_event(PlaceChanged(place_from_pb2(update.place)))
        elif kind == "del_place":
            self._on_event(PlaceDeleted(update.del_place))
        elif kind == "resource":
            self._on_event(ResourceChanged(resource_from_pb2(update.resource)))
        elif kind == "del_resource":
            path = update.del_resource
            self._on_event(
                ResourceDeleted(path.exporter_name, path.group_name, path.resource_name)
            )
        else:
            logger.warning("unknown update kind from coordinator: %s", kind)
