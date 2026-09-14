import asyncio

from textual.screen import ModalScreen
from textual.widgets import Static

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stream import ConnectionChanged, ConnState, RetryScheduled
from labgrid_tui.ui.app import CONNECTION_GRACE_SECONDS, FleetEvent, LabgridTuiApp
from labgrid_tui.ui.screens.connection_overlay import ConnectionOverlay
from tests.fake_coordinator import FakeCoordinator


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


def test_grace_constant() -> None:
    assert CONNECTION_GRACE_SECONDS == 5.0


async def test_overlay_appears_on_sustained_drop_and_clears_on_live(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if app.store.conn.name == "LIVE":
                break
        servicer.drop_streams = True
        for q in servicer.client_queues:
            q.put_nowait(None)  # end current stream -> DISCONNECTED
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if app.store.conn.name != "LIVE":
                break
        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)
        servicer.drop_streams = False
        for _ in range(80):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if not isinstance(app.screen, ConnectionOverlay):
                break
        assert not isinstance(app.screen, ConnectionOverlay)


async def test_buried_overlay_dismissed_on_live(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: a screen pushed on top of the ConnectionOverlay (e.g. the
    built-in command palette, reachable via an App-level binding even while
    a modal is active) buried it below the stack top; the LIVE handler
    only ever checked `self.screen`, so it never got cleaned up."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.store.conn = ConnState.DISCONNECTED
        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)
        overlay = app.screen

        app.push_screen(ModalScreen())
        await pilot.pause()
        assert app.screen is not overlay
        assert overlay in app.screen_stack

        app.post_message(FleetEvent(ConnectionChanged(state=ConnState.LIVE)))
        await pilot.pause()
        assert overlay not in app.screen_stack


async def test_escape_quits_app(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Escape on the connection-lost overlay quits outright; it does not
    dismiss-and-keep-browsing-stale-data."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.store.conn = ConnState.DISCONNECTED
        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)

        await pilot.press("escape")
        await pilot.pause()
        assert app._exit is True


async def test_q_quits_app_from_overlay(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """q is bound locally on ConnectionOverlay: Textual's modal binding
    chain stops at the modal screen, so App-level BINDINGS never reach it."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.store.conn = ConnState.DISCONNECTED
        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)

        await pilot.press("q")
        await pilot.pause()
        assert app._exit is True


async def test_countdown_updates_from_retry_scheduled_and_ticks(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.store.conn = ConnState.DISCONNECTED
        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)

        app.post_message(FleetEvent(RetryScheduled(delay=3.0, attempt=2)))
        await pilot.pause()
        status = app.screen.query_one("#conn-status", Static)
        assert "3s" in str(status.content)
        assert "attempt 2" in str(status.content)

        await asyncio.sleep(1.1)
        await pilot.pause()
        status = app.screen.query_one("#conn-status", Static)
        assert "2s" in str(status.content)

        app.post_message(FleetEvent(ConnectionChanged(state=ConnState.CONNECTING)))
        await pilot.pause()
        status = app.screen.query_one("#conn-status", Static)
        assert "Connecting" in str(status.content)


async def test_overlay_pushed_after_retry_scheduled_shows_countdown_immediately(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Regression: RetryScheduled/ConnectionChanged(CONNECTING) were only
    forwarded to an overlay that already existed. An overlay pushed later
    (e.g. once the grace timer fires) must still seed its display from the
    last such event instead of showing the static placeholder until the
    next per-second tick or retry cycle."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.store.conn = ConnState.DISCONNECTED
        app.post_message(FleetEvent(RetryScheduled(delay=10.0, attempt=3)))
        await pilot.pause()
        assert not isinstance(app.screen, ConnectionOverlay)

        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)
        status = app.screen.query_one("#conn-status", Static)
        content = str(status.content)
        assert "retrying with backoff" not in content
        assert "attempt 3" in content
        assert "10s" in content or "9s" in content


async def test_retry_now_key_calls_app_retry_connection(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    calls: list[bool] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.retry_connection = lambda: calls.append(True)  # type: ignore[method-assign]
        app.store.conn = ConnState.DISCONNECTED
        app._maybe_show_connection_overlay()
        await pilot.pause()
        assert isinstance(app.screen, ConnectionOverlay)

        await pilot.press("r")
        await pilot.pause()
        assert calls == [True]


def _laid_out_widths(screen: ModalScreen[None]) -> dict[str, int]:
    # What the compositor actually placed, not what the widget tree says:
    # a widget that resolves to zero columns is simply absent here.
    return {
        widget.id or "": region.width
        for widget, (region, *_rest) in screen._compositor.visible_widgets.items()
        if widget is not screen
    }


async def test_overlay_content_is_laid_out_with_columns() -> None:
    # Regression: an auto-width modal whose children default to 1fr
    # collapsed to its border and padding, rendering an empty box.
    app = LabgridTuiApp(_config("127.0.0.1:1"))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(ConnectionOverlay("127.0.0.1:1", "flag"))
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ConnectionOverlay)
        widths = _laid_out_widths(screen)
        for widget_id in ("conn-text", "conn-status", "conn-hint"):
            assert widths.get(widget_id, 0) >= 40, widths
        assert "unreachable" in str(screen.query_one("#conn-text", Static).render())
