import asyncio

import pytest

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.commands import CommandEntry
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.widgets.device_table import DeviceTable
from tests.fake_coordinator import FakeCoordinator


class _RecordingRunner:
    def __init__(self) -> None:
        self.ran: list[CommandEntry] = []

    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        self.ran.append(entry)

    def copy(self, entry: CommandEntry) -> None:
        pass


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


async def _ready(app: LabgridTuiApp, pilot: object, n: int) -> DeviceTable:
    for _ in range(60):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        table = app.screen.query_one(DeviceTable)
        if table.row_count >= n:
            return table
    raise AssertionError("table never populated")


async def test_acquire_on_cursor_row(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    runner = _RecordingRunner()
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        app.screen._runner = runner  # type: ignore[attr-defined]
        table.focus()
        await pilot.press("r")
        await pilot.pause()
        assert [e.template.label for e in runner.ran] == ["Acquire"]
        assert "acquire" in runner.ran[0].command_line


async def test_acquire_on_place_reserved_by_other_is_refused(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """`r` on a place someone else has reserved must not acquire it: the
    coordinator's AcquirePlace would refuse with PERMISSION_DENIED."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", reservation="TOK"))
    servicer.reservations.append(pb2.Reservation(owner="host9/carol", token="TOK", state=0))
    app = LabgridTuiApp(_config(address))
    runner = _RecordingRunner()
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        await app._poll_reservations()
        app.screen._runner = runner  # type: ignore[attr-defined]
        notifications: list[str] = []
        app.notify = lambda message, **_kwargs: notifications.append(message)  # type: ignore[method-assign]
        table.focus()
        await pilot.press("r")
        await pilot.pause()
        assert runner.ran == []
        assert notifications == ["reserved by host9/carol"]


async def test_bulk_release_skips_invalid(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))  # free
    servicer.places.append(pb2.Place(name="tb-b", acquired="x/y"))  # other user
    app = LabgridTuiApp(_config(address))
    runner = _RecordingRunner()
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 2)
        app.screen._runner = runner  # type: ignore[attr-defined]
        table.marks.update({"tb-a", "tb-b"})
        table.focus()
        await pilot.press("R")
        await pilot.pause()
        # Release requires usable-by-me: free place and other-user place both skip
        assert runner.ran == []


async def test_bulk_dispatch_counts_vanished_targets_as_skipped(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    runner = _RecordingRunner()
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 2)
        app.screen._runner = runner  # type: ignore[attr-defined]
        table.marks.update({"tb-a", "tb-b"})
        # Both places vanish from the store (e.g. deleted upstream) without a
        # table refresh in between, so the marks still target them.
        app.store.places.clear()
        notifications: list[str] = []
        app.notify = lambda message, **_kwargs: notifications.append(message)  # type: ignore[method-assign]
        table.focus()
        await pilot.press("r")
        await pilot.pause()
        assert runner.ran == []
        assert notifications == ["Acquire: 0 run, 2 skipped"]


async def test_single_vanished_target_notifies(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    runner = _RecordingRunner()
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        app.screen._runner = runner  # type: ignore[attr-defined]
        # Deterministic "vanished" simulation: the table row still exists,
        # but the entry lookup finds nothing (place gone from the store by
        # the time the verb resolves). Racing a store.clear() against the
        # 0.2s refresh flush made this flaky.
        app.screen._entries_for = lambda name: []  # type: ignore[attr-defined]
        notifications: list[str] = []
        app.notify = lambda message, **_kwargs: notifications.append(message)  # type: ignore[method-assign]
        table.focus()
        await pilot.press("r")
        await pilot.pause()
        assert runner.ran == []
        assert notifications == ["Acquire: tb-1 is gone"]


async def test_bulk_dispatch_posts_one_success_toast(
    fake_coordinator: tuple[FakeCoordinator, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bulk acquire over marked places is silent per item on success and
    posts exactly the one summary toast."""
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)

    async def fake_capture(line: str, on_line: object) -> int:
        return 0

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", fake_capture)
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 2)
        table.marks.update({"tb-a", "tb-b"})
        notifications: list[str] = []

        def record(message: str, **_kwargs: object) -> None:
            notifications.append(message)

        app.notify = record  # type: ignore[method-assign]
        table.focus()
        await pilot.press("r")
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.05)
        assert notifications.count("Acquire: 2 run, 0 skipped") == 1
        assert not any("succeeded" in n for n in notifications)


async def test_single_dispatch_posts_success_toast(
    fake_coordinator: tuple[FakeCoordinator, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single-target dispatch keeps the per-command success toast."""
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)

    async def fake_capture(line: str, on_line: object) -> int:
        return 0

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", fake_capture)
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        notifications: list[str] = []

        def record(message: str, **_kwargs: object) -> None:
            notifications.append(message)

        app.notify = record  # type: ignore[method-assign]
        table.focus()
        await pilot.press("r")
        for _ in range(20):
            await pilot.pause()
            await asyncio.sleep(0.05)
        assert any("succeeded" in n for n in notifications)


async def test_copy_row_yanks_place_name(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        copied: list[str] = []
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        table.focus()
        await pilot.press("y")
        await pilot.pause()
        assert copied == ["tb-1"]


@pytest.mark.first_run
async def test_first_run_hints_once(
    fake_coordinator: tuple[FakeCoordinator, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app._ui_state.onboarded is True  # type: ignore[attr-defined]
    import os

    from labgrid_tui.ui.uistate import load_state, state_path

    assert load_state(state_path(os.environ)).onboarded is True
