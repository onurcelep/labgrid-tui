import asyncio
import contextlib

import grpc.aio

from labgrid_tui.coordinator.client import CHANNEL_OPTIONS
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    EventStream,
    PlaceChanged,
    PlaceDeleted,
    ResourceChanged,
    ResourceDeleted,
    RetryScheduled,
)
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from tests.fake_coordinator import FakeCoordinator


class Recorder:
    def __init__(self) -> None:
        self.events: list[Event] = []
        self.live = asyncio.Event()

    def __call__(self, event: Event) -> None:
        self.events.append(event)
        if isinstance(event, ConnectionChanged) and event.state is ConnState.LIVE:
            self.live.set()


async def _run_stream(
    address: str, recorder: Recorder
) -> tuple[EventStream, asyncio.Task[None], grpc.aio.Channel]:
    channel = grpc.aio.insecure_channel(address, options=CHANNEL_OPTIONS)
    stream = EventStream(channel, recorder, client_name="testhost/tester", version="0.1.0")
    task = asyncio.ensure_future(stream.run())
    return stream, task, channel


async def _stop(stream: EventStream, task: "asyncio.Task[None]", channel: grpc.aio.Channel) -> None:
    stream.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    await channel.close()


async def test_handshake_order_and_replay(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    resource = pb2.Resource(cls="NetworkSerialPort", avail=True)
    resource.path.exporter_name = "exp1"
    resource.path.group_name = "g1"
    resource.path.resource_name = "serial"
    servicer.resources.append(resource)

    recorder = Recorder()
    stream, task, channel = await _run_stream(address, recorder)
    await asyncio.wait_for(recorder.live.wait(), timeout=5)
    await _stop(stream, task, channel)

    assert servicer.handshake_log[:4] == ["startup", "subscribe", "subscribe", "sync"]
    kinds = [type(e) for e in recorder.events]
    # CONNECTING first, replay events before LIVE
    assert kinds[0] is ConnectionChanged
    assert PlaceChanged in kinds and ResourceChanged in kinds
    assert kinds.index(PlaceChanged) < len(kinds) - 1
    live_index = max(
        i
        for i, e in enumerate(recorder.events)
        if isinstance(e, ConnectionChanged) and e.state is ConnState.LIVE
    )
    assert all(
        not isinstance(e, (PlaceChanged, ResourceChanged))
        for e in recorder.events[live_index + 1 :]
    )


async def test_pushed_updates_translate(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    recorder = Recorder()
    stream, task, channel = await _run_stream(address, recorder)
    await asyncio.wait_for(recorder.live.wait(), timeout=5)

    update = pb2.UpdateResponse()
    update.place.name = "tb-new"
    servicer.push(update)

    deletion = pb2.UpdateResponse()
    deletion.del_place = "tb-old"
    servicer.push(deletion)

    res_del = pb2.UpdateResponse()
    res_del.del_resource.exporter_name = "exp1"
    res_del.del_resource.group_name = "g1"
    res_del.del_resource.resource_name = "serial"
    servicer.push(res_del)

    for _ in range(50):
        await asyncio.sleep(0.05)
        if any(isinstance(e, ResourceDeleted) for e in recorder.events):
            break
    await _stop(stream, task, channel)

    assert any(isinstance(e, PlaceChanged) and e.place.name == "tb-new" for e in recorder.events)
    assert any(isinstance(e, PlaceDeleted) and e.name == "tb-old" for e in recorder.events)
    assert any(
        isinstance(e, ResourceDeleted) and (e.exporter, e.group, e.name) == ("exp1", "g1", "serial")
        for e in recorder.events
    )


async def test_reconnect_after_drop(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.drop_streams = True
    recorder = Recorder()
    stream, task, channel = await _run_stream(address, recorder)
    await asyncio.sleep(0.5)  # let at least one attempt fail
    servicer.drop_streams = False
    await asyncio.wait_for(recorder.live.wait(), timeout=10)
    await _stop(stream, task, channel)

    states = [e.state for e in recorder.events if isinstance(e, ConnectionChanged)]
    assert ConnState.DISCONNECTED in states
    assert states[-1] is ConnState.LIVE


async def test_retry_scheduled_emitted_before_backoff_sleep(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.drop_streams = True
    recorder = Recorder()
    stream, task, channel = await _run_stream(address, recorder)
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any(isinstance(e, RetryScheduled) for e in recorder.events):
                break
        retries = [e for e in recorder.events if isinstance(e, RetryScheduled)]
        assert retries
        assert retries[0].delay > 0
        assert retries[0].attempt == 1
        # RetryScheduled must precede the sleep, i.e. arrive right after
        # DISCONNECTED and before the next CONNECTING attempt.
        disconnected_index = next(
            i
            for i, e in enumerate(recorder.events)
            if isinstance(e, ConnectionChanged) and e.state is ConnState.DISCONNECTED
        )
        retry_index = next(
            i for i, e in enumerate(recorder.events) if isinstance(e, RetryScheduled)
        )
        assert disconnected_index < retry_index
    finally:
        await _stop(stream, task, channel)


async def test_retry_now_shortens_backoff_wait(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.drop_streams = True
    recorder = Recorder()
    stream, task, channel = await _run_stream(address, recorder)
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any(isinstance(e, RetryScheduled) for e in recorder.events):
                break
        first = next(e for e in recorder.events if isinstance(e, RetryScheduled))
        assert first.delay >= 0.5  # sanity: not already instant

        loop = asyncio.get_event_loop()
        start = loop.time()
        stream.retry_now()

        for _ in range(100):
            await asyncio.sleep(0.02)
            retries = [e for e in recorder.events if isinstance(e, RetryScheduled)]
            if len(retries) >= 2:
                break
        elapsed = loop.time() - start

        assert len(retries) >= 2
        # retry_now() must cut the sleep short well before the scheduled delay.
        assert elapsed < first.delay - 0.2
    finally:
        await _stop(stream, task, channel)


async def test_manual_retry_does_not_advance_backoff(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """retry_now() cutting a backoff wait short must not grow the delay
    the next automatic retry sees: only a wait that ran its full course
    reflects a coordinator that is genuinely still unreachable."""
    servicer, address = fake_coordinator
    servicer.drop_streams = True
    recorder = Recorder()
    stream, task, channel = await _run_stream(address, recorder)
    try:
        for _ in range(100):
            await asyncio.sleep(0.05)
            if any(isinstance(e, RetryScheduled) for e in recorder.events):
                break
        first = next(e for e in recorder.events if isinstance(e, RetryScheduled))

        stream.retry_now()

        retries: list[RetryScheduled] = []
        for _ in range(100):
            await asyncio.sleep(0.02)
            retries = [e for e in recorder.events if isinstance(e, RetryScheduled)]
            if len(retries) >= 2:
                break

        assert len(retries) >= 2
        assert retries[1].delay == first.delay
    finally:
        await _stop(stream, task, channel)
