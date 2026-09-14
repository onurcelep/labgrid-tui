"""In-process fake coordinator for wire-layer tests."""

import asyncio
import contextlib
from collections.abc import AsyncIterator

import grpc

from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2_grpc as pb2_grpc


class FakeCoordinator(pb2_grpc.CoordinatorServicer):
    def __init__(self) -> None:
        self.places: list[pb2.Place] = []
        self.resources: list[pb2.Resource] = []
        self.reservations: list[pb2.Reservation] = []
        self.fail_unary = False          # inject UNAVAILABLE on unary reads
        self.drop_streams = False        # abort ClientStream sessions immediately
        self.handshake_log: list[str] = []
        self.client_queues: list[asyncio.Queue[pb2.ClientOutMessage | None]] = []

    def push(self, update: pb2.UpdateResponse) -> None:
        """Publish one update to every connected stream client."""
        msg = pb2.ClientOutMessage()
        msg.updates.append(update)
        for q in self.client_queues:
            q.put_nowait(msg)

    async def GetPlaces(
        self, request: pb2.GetPlacesRequest, context: grpc.aio.ServicerContext
    ) -> pb2.GetPlacesResponse:
        if self.fail_unary:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "injected failure")
        return pb2.GetPlacesResponse(places=self.places)

    async def GetReservations(
        self, request: pb2.GetReservationsRequest, context: grpc.aio.ServicerContext
    ) -> pb2.GetReservationsResponse:
        if self.fail_unary:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "injected failure")
        return pb2.GetReservationsResponse(reservations=self.reservations)

    async def ClientStream(
        self,
        request_iterator: AsyncIterator[pb2.ClientInMessage],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb2.ClientOutMessage]:
        if self.drop_streams:
            await context.abort(grpc.StatusCode.UNAVAILABLE, "injected drop")
        queue: asyncio.Queue[pb2.ClientOutMessage | None] = asyncio.Queue()
        self.client_queues.append(queue)

        async def reader() -> None:
            async for in_msg in request_iterator:
                kind = in_msg.WhichOneof("kind")
                self.handshake_log.append(str(kind))
                if kind == "subscribe":
                    out = pb2.ClientOutMessage()
                    if in_msg.subscribe.all_places:
                        for p in self.places:
                            out.updates.add().place.CopyFrom(p)
                    if in_msg.subscribe.all_resources:
                        for r in self.resources:
                            out.updates.add().resource.CopyFrom(r)
                    if out.updates:
                        queue.put_nowait(out)
                elif kind == "sync":
                    out = pb2.ClientOutMessage()
                    out.sync.id = in_msg.sync.id
                    queue.put_nowait(out)
            queue.put_nowait(None)

        reader_task = asyncio.ensure_future(reader())
        try:
            while True:
                out_msg = await queue.get()
                if out_msg is None:
                    return
                yield out_msg
        finally:
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reader_task
            self.client_queues.remove(queue)


async def start_fake(servicer: FakeCoordinator) -> tuple[grpc.aio.Server, str]:
    server = grpc.aio.server()
    pb2_grpc.add_CoordinatorServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, f"127.0.0.1:{port}"
