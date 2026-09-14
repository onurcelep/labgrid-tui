"""Pilot tests for the coordinator selector and its create/edit/delete
modals, runtime switching between coordinators, and the palette entries
switching adds."""

import asyncio
import os

from textual.widgets import Button, Input, Label, ListView, Static

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.coordinators import (
    CoordinatorEntry,
    Coordinators,
    default_coordinators_path,
    load_coordinators,
    save_coordinators,
)
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.palette import CommandProvider
from labgrid_tui.ui.screens.coordinator_delete import CoordinatorDeleteConfirm
from labgrid_tui.ui.screens.coordinator_edit import CoordinatorEditModal
from labgrid_tui.ui.screens.coordinator_selector import CoordinatorSelector
from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.device_table import DeviceTable
from tests.fake_coordinator import FakeCoordinator, start_fake


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


def _write_coordinators(current: str | None, **entries: str) -> None:
    save_coordinators(
        default_coordinators_path(os.environ),
        Coordinators(
            current=current,
            entries={
                name: CoordinatorEntry(name=name, address=address)
                for name, address in entries.items()
            },
        ),
    )


def _activity_text(app: LabgridTuiApp) -> str:
    log = app.screen.query_one(ActivityLog)
    return "\n".join(strip.text for strip in log.lines)


async def _ready(app: LabgridTuiApp, pilot: object, n: int) -> DeviceTable:
    for _ in range(80):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        table = app.screen.query_one(DeviceTable)
        if table.row_count >= n:
            return table
    raise AssertionError("table never populated")


async def _wait_until(pilot: object, predicate: object, attempts: int = 80) -> bool:
    for _ in range(attempts):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        if predicate():  # type: ignore[operator]
            return True
    return False


# ---------------------------------------------------------------------
# Selector
# ---------------------------------------------------------------------


async def test_selector_opens_with_list_focus(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address)
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorSelector)
        list_view = app.screen.query_one("#coord-list", ListView)
        assert app.focused is list_view


async def test_selector_shows_current_marker(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Entries are listed alphabetically; "home" sorts before "lab", so
    the cursor starts on "lab" (the current one) at index 1."""
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address, home="127.0.0.1:1")
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        list_view = app.screen.query_one("#coord-list", ListView)
        assert list_view.index == 1
        labels = [str(label.content) for label in list_view.query(Label)]
        assert any("home" in text and "*" not in text for text in labels)
        assert any("lab *" in text for text in labels)


async def test_escape_closes_selector(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address)
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorSelector)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CoordinatorSelector)


# ---------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------


async def test_create_through_modal_writes_file(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorEditModal)

        await pilot.press(*"lab")
        await pilot.press("enter")
        await pilot.press(*"127.0.0.1:9999")
        await pilot.press("enter")
        await pilot.press(*"mycli")
        await pilot.press("enter")
        await pilot.pause()

        assert not isinstance(app.screen, CoordinatorEditModal)
        coordinators = load_coordinators(app.coordinators_path)
        assert coordinators.entries["lab"].address == "127.0.0.1:9999"
        assert coordinators.entries["lab"].prefix == "mycli"
        # In-memory app state updated too, not just the file on disk.
        assert app.coordinators.entries["lab"].address == "127.0.0.1:9999"


async def test_create_rejects_invalid_address_inline(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()

        await pilot.press(*"lab")
        await pilot.press("enter")  # -> address field
        await pilot.press(*"no-port-here")
        await pilot.press("enter")  # -> prefix field (not a submit yet)
        await pilot.press("enter")  # empty prefix: submits, validates all fields
        await pilot.pause()

        # Rejected: modal stays open, nothing written.
        assert isinstance(app.screen, CoordinatorEditModal)
        error = app.screen.query_one("#coord-edit-error", Static)
        assert str(error.content)
        coordinators = load_coordinators(app.coordinators_path)
        assert "lab" not in coordinators.entries


# ---------------------------------------------------------------------
# Edit
# ---------------------------------------------------------------------


async def test_edit_through_modal_updates_file(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators(None, lab="lab.example:20408")
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorEditModal)

        name_input = app.screen.query_one("#coord-edit-name", Input)
        assert name_input.disabled  # renaming is not supported

        address_input = app.screen.query_one("#coord-edit-address", Input)
        assert app.focused is address_input  # locked name field is skipped on mount
        address_input.clear()
        await pilot.press(*"9.9.9.9:1234")
        await pilot.press("enter")
        await pilot.press("enter")  # empty prefix, submits
        await pilot.pause()

        assert not isinstance(app.screen, CoordinatorEditModal)
        coordinators = load_coordinators(app.coordinators_path)
        assert coordinators.entries["lab"].address == "9.9.9.9:1234"


# ---------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------


async def test_delete_with_confirm_removes_entry(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Entries sort alphabetically ("home" before "lab"); the cursor opens
    on "lab" (current), so moving up once selects "home"."""
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address, home="127.0.0.1:1")
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        list_view = app.screen.query_one("#coord-list", ListView)
        assert list_view.index == 1
        await pilot.press("up")
        await pilot.pause()
        assert list_view.index == 0

        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorDeleteConfirm)
        await pilot.press("y")
        await pilot.pause()

        assert not isinstance(app.screen, CoordinatorDeleteConfirm)
        coordinators = load_coordinators(app.coordinators_path)
        assert "home" not in coordinators.entries
        assert "lab" in coordinators.entries


async def test_delete_cancel_keeps_entry(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address, home="127.0.0.1:1")
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()

        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorDeleteConfirm)
        await pilot.press("n")
        await pilot.pause()

        assert not isinstance(app.screen, CoordinatorDeleteConfirm)
        coordinators = load_coordinators(app.coordinators_path)
        assert len(coordinators.entries) == 2


async def test_delete_confirm_button_is_error_variant(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address, home="127.0.0.1:1")
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("up")
        await pilot.press("x")
        await pilot.pause()
        button = app.screen.query_one("#coord-delete-confirm", Button)
        assert button.variant == "error"


# ---------------------------------------------------------------------
# Runtime switch: leak-free reconnect between two coordinators
# ---------------------------------------------------------------------


async def test_switch_between_two_coordinators_swaps_fleet_and_cleans_up() -> None:
    servicer_a = FakeCoordinator()
    servicer_a.places.append(pb2.Place(name="tb-a"))
    server_a, address_a = await start_fake(servicer_a)

    servicer_b = FakeCoordinator()
    servicer_b.places.append(pb2.Place(name="tb-b"))
    server_b, address_b = await start_fake(servicer_b)

    try:
        _write_coordinators("a", a=address_a, b=address_b)
        app = LabgridTuiApp(_config(address_a))
        async with app.run_test() as pilot:
            table = await _ready(app, pilot, 1)
            await pilot.press("a")  # activity log is hidden by default
            await pilot.pause()
            assert "tb-a" in app.store.places
            assert "tb-b" not in app.store.places

            table.marks.add("tb-a")
            old_worker = app._stream_worker
            old_client = app.client

            app.switch_coordinator("b")
            assert await _wait_until(pilot, lambda: app._stream_worker is not old_worker)
            assert await _wait_until(pilot, lambda: "tb-b" in app.store.places)

            assert "tb-a" not in app.store.places  # old fleet cleared, not merged
            assert table.marks == set()  # marks were cleared as part of the reset

            # Header/status reflect the new coordinator.
            assert app.config.coordinator == address_b
            assert app.config.coordinator_source == "coordinator b"
            assert app.coordinators.current == "b"

            # The old worker/channel are torn down, not leaked.
            assert old_worker is not None
            assert old_worker.is_finished
            assert app.client is not old_client
            assert servicer_a.client_queues == []

            # Persisted to disk too.
            on_disk = load_coordinators(app.coordinators_path)
            assert on_disk.current == "b"

            # Activity log records the switch.
            activity = _activity_text(app)
            assert "coordinator switched to b" in activity
            assert address_b in activity
    finally:
        await server_a.stop(grace=None)
        await server_b.stop(grace=None)


async def test_switch_unknown_name_is_a_noop() -> None:
    server, address = await start_fake(FakeCoordinator())
    try:
        _write_coordinators("a", a=address)
        app = LabgridTuiApp(_config(address))
        async with app.run_test() as pilot:
            await pilot.pause()
            worker_before = app._stream_worker
            await app._do_switch_coordinator("nosuch")
            await pilot.pause()
            assert app._stream_worker is worker_before
            assert app.config.coordinator == address
    finally:
        await server.stop(grace=None)


async def test_editing_active_coordinator_triggers_reconnect() -> None:
    """Changing the active coordinator's own address must reconnect the
    running session to it, not just update the file on disk."""
    servicer_a = FakeCoordinator()
    servicer_a.places.append(pb2.Place(name="tb-a"))
    server_a, address_a = await start_fake(servicer_a)

    servicer_b = FakeCoordinator()
    servicer_b.places.append(pb2.Place(name="tb-b"))
    server_b, address_b = await start_fake(servicer_b)

    try:
        _write_coordinators("a", a=address_a)
        app = LabgridTuiApp(_config(address_a))
        async with app.run_test() as pilot:
            await _ready(app, pilot, 1)
            assert "tb-a" in app.store.places

            await pilot.press("P")
            await pilot.pause()
            await pilot.press("e")
            await pilot.pause()
            assert isinstance(app.screen, CoordinatorEditModal)

            address_input = app.screen.query_one("#coord-edit-address", Input)
            address_input.clear()
            await pilot.press(*address_b)
            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause()

            assert await _wait_until(pilot, lambda: "tb-b" in app.store.places)
            assert "tb-a" not in app.store.places
            assert app.config.coordinator == address_b
    finally:
        await server_a.stop(grace=None)
        await server_b.stop(grace=None)


# ---------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------


async def test_palette_offers_switch_and_manage(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address, home="127.0.0.1:1")
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = CommandProvider(app.screen)
        names = [str(hit.display) async for hit in provider.discover()]
        assert "Coordinator: manage" in names
        assert "Coordinator: switch to home" in names
        # The current coordinator is not offered as a switch target.
        assert "Coordinator: switch to lab" not in names


async def test_palette_switch_entry_invokes_switch(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    _write_coordinators("lab", lab=address, home="127.0.0.1:1")
    app = LabgridTuiApp(_config(address))
    calls: list[str] = []
    async with app.run_test() as pilot:
        await pilot.pause()
        app.switch_coordinator = calls.append  # type: ignore[method-assign]
        provider = CommandProvider(app.screen)
        hits = [hit async for hit in provider.search("switch to home")]
        assert hits
        hits[0].command()
        await pilot.pause()
        assert calls == ["home"]


async def test_palette_no_coordinators_offers_manage_but_no_switch(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """ "manage" is always offered (it's how you add the first one); with
    no entries there is nothing to switch to."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = CommandProvider(app.screen)
        names = [str(hit.display) async for hit in provider.discover()]
        assert "Coordinator: manage" in names
        assert not any(name.startswith("Coordinator: switch to") for name in names)


async def test_delete_confirm_content_is_laid_out_with_columns(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    from labgrid_tui.ui.screens.coordinator_delete import CoordinatorDeleteConfirm

    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.push_screen(CoordinatorDeleteConfirm("prod"))
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, CoordinatorDeleteConfirm)
        widths = {
            widget.id or "": region.width
            for widget, (region, *_rest) in screen._compositor.visible_widgets.items()
            if widget is not screen
        }
        assert widths.get("coord-delete-title", 0) >= 30, widths
        assert widths.get("coord-delete-message", 0) >= 30, widths
