import asyncio
from datetime import UTC, datetime

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Static

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.identity import current_id
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.widgets.device_table import DeviceTable
from tests.fake_coordinator import FakeCoordinator


def _config(address: str) -> Config:
    return Config(
        coordinator=address,
        coordinator_source="flag",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


def _serial(exporter: str = "exp1") -> pb2.Resource:
    resource = pb2.Resource(cls="NetworkSerialPort", avail=True)
    resource.path.exporter_name = exporter
    resource.path.group_name = "g1"
    resource.path.resource_name = "serial"
    return resource


async def _open_detail(app: LabgridTuiApp, pilot: object, name: str) -> DetailOverlay:
    for _ in range(80):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        table = app.screen.query_one(DeviceTable)
        if name in app.store.places and table.row_count:
            break
    app.screen.query_one(DeviceTable).focus()
    await pilot.press("d")  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    assert isinstance(app.screen, DetailOverlay)
    return app.screen


async def test_detail_shows_place_info(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    place = pb2.Place(name="tb-1", comment="bench one", tags={"env": "dev"}, acquired="host/alice")
    place.matches.add(exporter="exp1", group="g1", cls="*")
    servicer.places.append(place)
    servicer.resources.append(_serial())
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        overlay = await _open_detail(app, pilot, "tb-1")
        title = str(overlay.query_one("#detail-title", Static).render())
        body = str(overlay.query_one("#detail-body", Static).render())
        assert "tb-1" in title
        assert "bench one" in body
        assert "env=dev" in body
        assert "host/alice" in body
        assert "State:" in body and "Acquired" in body
        assert "Resources" in body
        assert "serial" in body and "NetworkSerialPort" in body
        assert "(online)" in body
        assert "SER=console" in body


async def test_detail_shows_my_reservation_owner_and_state(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", reservation="TOK"))
    servicer.reservations.append(pb2.Reservation(owner=current_id(), token="TOK", state=1))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._poll_reservations()
        overlay = await _open_detail(app, pilot, "tb-1")
        body = str(overlay.query_one("#detail-body", Static).render())
        assert "TOK" in body
        assert "allocated" in body
        assert "(yours)" in body


async def test_detail_shows_other_users_reservation_without_yours_marker(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", reservation="TOK"))
    servicer.reservations.append(pb2.Reservation(owner="host9/carol", token="TOK", state=0))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app._poll_reservations()
        overlay = await _open_detail(app, pilot, "tb-1")
        body = str(overlay.query_one("#detail-body", Static).render())
        assert "host9/carol" in body
        assert "(yours)" not in body


async def test_copy_details(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", comment="bench"))
    app = LabgridTuiApp(_config(address))
    copied: list[str] = []
    async with app.run_test() as pilot:
        overlay = await _open_detail(app, pilot, "tb-1")
        app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
        await pilot.press("y")
        await pilot.pause()
        assert copied and "tb-1" in copied[0] and "bench" in copied[0]
        assert isinstance(app.screen, DetailOverlay)  # copy does not close
        assert overlay is app.screen


async def test_c_opens_command_overlay_for_same_place(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _open_detail(app, pilot, "tb-1")
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        assert app.screen.place_name == "tb-1"


async def test_place_removed_while_open(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        overlay = await _open_detail(app, pilot, "tb-1")
        deletion = pb2.UpdateResponse()
        deletion.del_place = "tb-1"
        servicer.push(deletion)
        for _ in range(40):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if "tb-1" not in app.store.places:
                break
        for _ in range(6):
            await pilot.pause()
            await asyncio.sleep(0.1)
        title = str(overlay.query_one("#detail-title", Static).render())
        assert "removed" in title
        assert isinstance(app.screen, DetailOverlay)  # stays until dismissed


async def test_created_changed_are_absolute_utc_timestamps(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """The detail body shows absolute UTC timestamps; the table keeps
    relative ages (untouched here)."""
    from labgrid_tui.ui.screens.detail_overlay import _format_timestamp

    servicer, address = fake_coordinator
    created = 1_700_000_000.0
    changed = 1_700_003_600.0
    place = pb2.Place(name="tb-1", created=created, changed=changed)
    servicer.places.append(place)
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        overlay = await _open_detail(app, pilot, "tb-1")
        body = str(overlay.query_one("#detail-body", Static).render())
        assert f"Created:      {_format_timestamp(created)}" in body
        assert f"Changed:      {_format_timestamp(changed)}" in body
        # Absolute, not relative: no "ago" suffix anywhere near the dates.
        assert "ago" not in body


def test_format_timestamp_uses_utc() -> None:
    from labgrid_tui.ui.screens.detail_overlay import _format_timestamp

    epoch = 1_700_000_000.0
    expected = datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    assert _format_timestamp(epoch) == expected


async def test_shift_enter_copies_via_suspend(
    fake_coordinator: tuple[FakeCoordinator, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_copy_via_suspend(app: object, text: str, *, label: str = "text") -> None:
        calls.append((text, label))

    monkeypatch.setattr(
        "labgrid_tui.ui.screens.detail_overlay.copy_via_suspend",
        fake_copy_via_suspend,
    )
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", comment="bench"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _open_detail(app, pilot, "tb-1")
        await pilot.press("shift+enter")
        await pilot.pause()
        assert len(calls) == 1
        text, label = calls[0]
        assert "tb-1" in text and "bench" in text
        assert label == "details for tb-1"


async def test_resource_status_semantics(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Resource.acquired carries a PLACE name; render it as in-use/held-by."""
    servicer, address = fake_coordinator
    place = pb2.Place(name="tb-1", acquired="host/alice")
    place.matches.add(exporter="exp1", group="g1", cls="*")
    servicer.places.append(place)

    def _res(name: str, avail: bool, acquired: str) -> pb2.Resource:
        resource = pb2.Resource(cls="NetworkSerialPort", avail=avail, acquired=acquired)
        resource.path.exporter_name = "exp1"
        resource.path.group_name = "g1"
        resource.path.resource_name = name
        return resource

    servicer.resources.append(_res("mine", True, "tb-1"))
    servicer.resources.append(_res("elsewhere", True, "tb-other"))
    servicer.resources.append(_res("free", True, ""))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        overlay = await _open_detail(app, pilot, "tb-1")
        body = str(overlay.query_one("#detail-body", Static).render())
        assert "mine (in use)" in body
        assert "elsewhere (held by tb-other)" in body
        assert "free (online)" in body
        assert "acquired by tb-1" not in body


async def test_scroll_works_without_tab_and_focus_restored_on_close(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """A body taller than the modal must scroll from key 1, before any
    Tab press, and closing must return focus to the DeviceTable."""
    servicer, address = fake_coordinator
    # Enough overflow that one "down" press can't already reach the
    # bottom, regardless of exactly how tall #detail-modal is sized --
    # the assertions below need at least two more rows of scroll room.
    # A wildcard exporter match (instead of a single fixed exporter) is
    # required for every synthesized resource below to actually attach to
    # the place; a fixed exporter would leave 59 of the 60 unmatched.
    place = pb2.Place(name="tb-1", tags={f"tag{i}": f"value{i}" for i in range(20)})
    place.matches.add(exporter="*", group="g1", cls="*")
    servicer.places.append(place)
    for i in range(60):
        servicer.resources.append(_serial(exporter=f"exp{i}"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        overlay = await _open_detail(app, pilot, "tb-1")
        scroll = overlay.query_one("#detail-scroll", VerticalScroll)

        # Auto-focused on open, no Tab required.
        assert app.focused is scroll
        assert scroll.scroll_y == 0

        await pilot.press("down")
        await pilot.pause()
        assert scroll.scroll_y > 0
        after_down = scroll.scroll_y

        await pilot.press("j")
        await pilot.pause()
        assert scroll.scroll_y > after_down

        await pilot.press("pagedown")
        await pilot.pause()
        assert scroll.scroll_y > after_down

        await pilot.press("G")
        await pilot.pause()
        max_y = scroll.scroll_y
        assert max_y > 0

        await pilot.press("k")
        await pilot.pause()
        assert scroll.scroll_y < max_y

        await pilot.press("pageup")
        await pilot.pause()

        await pilot.press("g")
        await pilot.pause()
        assert scroll.scroll_y == 0

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, DetailOverlay)
        table = app.screen.query_one(DeviceTable)
        assert app.focused is table
