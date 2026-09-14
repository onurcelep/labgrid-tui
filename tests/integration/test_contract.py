"""Contract tests against a real labgrid coordinator.

Run: docker run -d -p 20408:20408 docker.io/labgrid/coordinator:latest
then: uv run pytest -m integration
Override the address with LABGRID_TUI_TEST_COORDINATOR.
"""

import asyncio
import contextlib
import os
import uuid

import grpc.aio
import pytest

from labgrid_tui.coordinator.client import CHANNEL_OPTIONS, CoordinatorClient
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    EventStream,
    PlaceChanged,
)
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2_grpc as pb2_grpc

pytestmark = pytest.mark.integration

ADDRESS = os.environ.get("LABGRID_TUI_TEST_COORDINATOR", "127.0.0.1:20408")


async def test_get_places_roundtrip() -> None:
    name = f"itest-{uuid.uuid4().hex[:8]}"
    channel = grpc.aio.insecure_channel(ADDRESS, options=CHANNEL_OPTIONS)
    stub = pb2_grpc.CoordinatorStub(channel)
    await stub.AddPlace(pb2.AddPlaceRequest(name=name), timeout=5)
    try:
        await stub.SetPlaceTags(
            pb2.SetPlaceTagsRequest(placename=name, tags={"board": "itest"}), timeout=5
        )
        await stub.SetPlaceComment(
            pb2.SetPlaceCommentRequest(placename=name, comment="from contract test"), timeout=5
        )
        await stub.AddPlaceMatch(
            pb2.AddPlaceMatchRequest(placename=name, pattern="*/itest/*"), timeout=5
        )
        client = CoordinatorClient(ADDRESS)
        try:
            places = {p.name: p for p in await client.get_places()}
        finally:
            await client.close()
        assert name in places
        place = places[name]
        assert place.tags == {"board": "itest"}
        assert place.comment == "from contract test"
        assert place.matches[0].group == "itest"
    finally:
        with contextlib.suppress(Exception):
            await stub.DeletePlace(pb2.DeletePlaceRequest(name=name), timeout=5)
        await channel.close()


async def test_stream_receives_place_updates() -> None:
    name = f"itest-{uuid.uuid4().hex[:8]}"
    events: list[Event] = []
    live = asyncio.Event()
    seen = asyncio.Event()

    def on_event(event: Event) -> None:
        events.append(event)
        if isinstance(event, ConnectionChanged) and event.state is ConnState.LIVE:
            live.set()
        if isinstance(event, PlaceChanged) and event.place.name == name:
            seen.set()

    channel = grpc.aio.insecure_channel(ADDRESS, options=CHANNEL_OPTIONS)
    stream = EventStream(channel, on_event, client_name="ci/contract-test", version="0.0.0")
    task = asyncio.ensure_future(stream.run())
    admin_channel = grpc.aio.insecure_channel(ADDRESS, options=CHANNEL_OPTIONS)
    stub = pb2_grpc.CoordinatorStub(admin_channel)
    try:
        await asyncio.wait_for(live.wait(), timeout=10)
        await stub.AddPlace(pb2.AddPlaceRequest(name=name), timeout=5)
        await asyncio.wait_for(seen.wait(), timeout=10)
    finally:
        with contextlib.suppress(Exception):
            await stub.DeletePlace(pb2.DeletePlaceRequest(name=name), timeout=5)
        stream.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await channel.close()
        await admin_channel.close()
