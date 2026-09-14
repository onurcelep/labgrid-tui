"""Wiring pilot test proving run_entry delegates through the ActionRunner
seam, plus the loop-affinity regression test. Branch coverage for the
dispatch matrix itself lives in test_actions.py."""

import asyncio
import os
from collections.abc import Callable
from dataclasses import replace

import pytest
from textual.widgets import Header

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState
from labgrid_tui.packs import PackRegistry, PackRegistryEntry, default_packs_path, save_registry
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.uistate import state_path
from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.status_bar import StatusBar
from tests.fake_coordinator import FakeCoordinator


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


class _RecordingRunner:
    def __init__(self) -> None:
        self.run_calls: list[CommandEntry] = []
        self.copy_calls: list[CommandEntry] = []

    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        self.run_calls.append(entry)

    def copy(self, entry: CommandEntry) -> None:
        self.copy_calls.append(entry)


async def test_run_entry_delegates_to_runner(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    fake_runner = _RecordingRunner()
    template = CommandTemplate("Place", "Show", "show")
    entry = CommandEntry(template, "labgrid-client -p tb-1 show", EntryState.RUNNABLE, None)
    async with app.run_test() as pilot:
        app.runner = fake_runner  # type: ignore[assignment]
        app.run_entry(entry)
        await pilot.pause()
    assert fake_runner.run_calls == [entry]
    assert fake_runner.copy_calls == []


async def test_app_constructed_outside_running_loop(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: main() constructs the app before app.run() starts the loop.

    grpc.aio binds its channel to the loop current at construction time, so
    building the client in __init__ attached every RPC to a loop that
    app.run() then replaced ("Task got Future attached to a different loop").
    Reproduce main()'s sequence exactly: construct the app on a temporary
    foreign loop in another thread, then run it on this loop and assert the
    stream still delivers.
    """
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-loop"))

    def _build() -> LabgridTuiApp:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return LabgridTuiApp(_config(address))
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    app = await asyncio.to_thread(_build)
    async with app.run_test() as pilot:
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if "tb-loop" in app.store.places:
                break
        assert "tb-loop" in app.store.places


async def test_unknown_persisted_theme_does_not_crash_construction(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: a stale ui.toml naming a theme Textual no longer knows
    about raised InvalidThemeError in __init__, before the TUI even
    started."""
    _, address = fake_coordinator
    path = state_path(os.environ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('theme = "no-such-theme"\n', encoding="utf-8")
    app = LabgridTuiApp(_config(address))
    assert app.theme != "no-such-theme"
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.theme != "no-such-theme"


async def test_extra_status_segments_appear_in_status_bar(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator

    class _ShellApp(LabgridTuiApp):
        def __init__(self, config: Config) -> None:
            super().__init__(config)
            self.extra_status_segments.append(lambda: "shell-segment")

    app = _ShellApp(_config(address))
    # Wide enough that the extension segment isn't dropped by the
    # priority-based status-bar shrinking (see test_dashboard_responsive.py).
    async with app.run_test(size=(140, 24)) as pilot:
        await pilot.pause()
        app.screen.refresh_fleet()  # type: ignore[attr-defined]
        await pilot.pause()
        from textual.widgets import Static

        bar = app.screen.query_one("#status-bar", Static)
        assert "shell-segment" in str(bar.render())


async def test_ctrl_c_quits_from_dashboard(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """ctrl+c must quit outright; Textual's inherited default (help_quit)
    only prints a hint and is not what a Vim/terminal user expects."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    exit_calls: list[object] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.exit = lambda *a, **kw: exit_calls.append((a, kw))  # type: ignore[method-assign]
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert len(exit_calls) == 1


async def test_ctrl_c_quits_from_detail_overlay(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """The App-level ctrl+c binding is priority=True, so it must reach a
    ModalScreen the same way Textual's own priority ctrl+q does."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    exit_calls: list[object] = []
    async with app.run_test() as pilot:
        for _ in range(80):
            await pilot.pause()
            await asyncio.sleep(0.05)
            table = app.screen.query_one(DeviceTable)
            if "tb-1" in app.store.places and table.row_count:
                break
        table.focus()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DetailOverlay)

        app.exit = lambda *a, **kw: exit_calls.append((a, kw))  # type: ignore[method-assign]
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert len(exit_calls) == 1


def test_app_title_and_subtitle() -> None:
    assert LabgridTuiApp.TITLE == "labgrid-tui"
    assert LabgridTuiApp.SUB_TITLE == "Device Dashboard"


async def test_header_present_above_status_bar(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.screen.query_one(Header) is not None
        # Header is the first composed widget, StatusBar right after it.
        children = list(app.screen.children)
        assert isinstance(children[0], Header)
        assert isinstance(children[1], StatusBar)


@pytest.mark.first_run
async def test_first_run_toasts_content_and_timing(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))

    timers: list[tuple[float, Callable[[], object]]] = []

    def _capture_timer(
        delay: float, callback: Callable[[], object], *args: object, **kwargs: object
    ) -> None:
        # Record without scheduling for real: the toasts fire at 0.5s/4.0s
        # and tests must not sit through that.
        timers.append((delay, callback))

    app.set_timer = _capture_timer  # type: ignore[method-assign]

    notifications: list[tuple[str, dict[str, object]]] = []

    def _notify(message: str, **kwargs: object) -> None:
        notifications.append((message, kwargs))

    app.notify = _notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()

    delays = [delay for delay, _ in timers]
    assert 0.5 in delays
    assert 4.0 in delays
    for delay, callback in timers:
        if delay in (0.5, 4.0):
            callback()

    assert len(notifications) == 2
    first_message, first_kwargs = notifications[0]
    assert "[bold]?[/]" in first_message
    assert "[bold]Ctrl+P[/]" in first_message
    assert first_kwargs.get("title") == "Welcome to labgrid-tui"
    assert first_kwargs.get("timeout") == 8

    second_message, second_kwargs = notifications[1]
    assert "[bold]r[/] acquire" in second_message
    assert "[bold]Space[/] mark" in second_message
    assert second_kwargs.get("timeout") == 8


async def test_config_error_shows_startup_toast(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    config = replace(_config(address), config_error="/tmp/config.toml: bad coordinator 'x'")
    app = LabgridTuiApp(config)

    notifications: list[tuple[str, dict[str, object]]] = []

    def _notify(message: str, **kwargs: object) -> None:
        notifications.append((message, kwargs))

    app.notify = _notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.pause()

    error_calls = [(m, k) for m, k in notifications if "bad coordinator" in m]
    assert error_calls
    message, kwargs = error_calls[0]
    assert "run: labgrid-tui config show" in message
    assert kwargs.get("severity") == "warning"
    assert kwargs.get("timeout") == 8


async def test_pack_load_error_shows_startup_toast_and_activity_line(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """A pack file that goes missing between `pack add` and startup (or
    was never valid) is skipped, not fatal: one toast, one activity line
    per broken pack, and the rest of the app keeps working."""
    _, address = fake_coordinator
    save_registry(
        default_packs_path(os.environ),
        PackRegistry(packs={"gone": PackRegistryEntry(name="gone", source="/no/such/robot.toml")}),
    )
    app = LabgridTuiApp(_config(address))
    assert app.packs == []
    assert app.pack_errors

    notifications: list[tuple[str, dict[str, object]]] = []

    def _notify(message: str, **kwargs: object) -> None:
        notifications.append((message, kwargs))

    app.notify = _notify  # type: ignore[method-assign]

    async with app.run_test() as pilot:
        await pilot.press("a")  # activity log is hidden by default
        await pilot.pause()
        text = _activity_text(app)

    error_calls = [(m, k) for m, k in notifications if "command pack" in m.lower()]
    assert error_calls
    message, kwargs = error_calls[0]
    assert "run: labgrid-tui pack list" in message
    assert kwargs.get("severity") == "warning"
    assert kwargs.get("timeout") == 8

    assert "pack ignored: pack gone:" in text
    assert "/no/such/robot.toml" in text


def _activity_text(app: LabgridTuiApp) -> str:
    log = app.screen.query_one(ActivityLog)
    return "\n".join(strip.text for strip in log.lines)


async def test_resource_deleted_logs_activity(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    resource = pb2.Resource(cls="NetworkSerialPort", avail=True)
    resource.path.exporter_name = "exp1"
    resource.path.group_name = "g1"
    resource.path.resource_name = "res1"
    servicer.resources.append(resource)
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.press("a")  # activity log is hidden by default
        await pilot.pause()
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.02)
            if ("exp1", "g1", "res1") in app.store.resources:
                break
        assert ("exp1", "g1", "res1") in app.store.resources

        deletion = pb2.UpdateResponse()
        deletion.del_resource.exporter_name = "exp1"
        deletion.del_resource.group_name = "g1"
        deletion.del_resource.resource_name = "res1"
        servicer.push(deletion)
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.02)
            if ("exp1", "g1", "res1") not in app.store.resources:
                break
        assert ("exp1", "g1", "res1") not in app.store.resources

        text = _activity_text(app)
        assert "exp1/g1/res1" in text
        assert "removed" in text


async def test_reservation_state_change_logs_activity(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.press("a")  # activity log is hidden by default
        await pilot.pause()

        # First poll only seeds the store silently: an empty
        # store.reservations at startup is indistinguishable from "the
        # coordinator genuinely has none", so nothing pre-existing should
        # log as newly appeared.
        await app._poll_reservations()
        await pilot.pause()
        assert _activity_text(app) == ""

        servicer.reservations.append(pb2.Reservation(owner="alice", token="tok1", state=0))
        await app._poll_reservations()
        await pilot.pause()
        text = _activity_text(app)
        assert "tok1" in text
        assert "waiting" in text

        servicer.reservations[0].state = 1  # allocated
        await app._poll_reservations()
        await pilot.pause()
        text = _activity_text(app)
        assert "allocated" in text

        before = _activity_text(app)
        await app._poll_reservations()
        await pilot.pause()
        assert _activity_text(app) == before  # unchanged poll logs nothing

        servicer.reservations.pop()
        await app._poll_reservations()
        await pilot.pause()
        text = _activity_text(app)
        assert "tok1" in text
        assert "gone" in text


async def test_reservation_allocated_logs_the_place_name(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """The allocations field names the place the reservation got; the
    activity line must surface it so the user sees which bench they got."""
    servicer, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.press("a")  # activity log is hidden by default
        await pilot.pause()

        await app._poll_reservations()  # seed silently

        servicer.reservations.append(pb2.Reservation(owner="alice", token="tok1", state=0))
        await app._poll_reservations()
        await pilot.pause()

        servicer.reservations[0].state = 1  # allocated
        servicer.reservations[0].allocations["main"] = "tb-1"
        await app._poll_reservations()
        await pilot.pause()
        text = _activity_text(app)
        assert "allocated" in text
        assert "(tb-1)" in text
