import asyncio
import os
from pathlib import Path

import pytest
from textual.command import CommandList, CommandPalette

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stubs import labgrid_coordinator_pb2 as pb2
from labgrid_tui.model.commands import CommandEntry
from labgrid_tui.model.identity import current_id
from labgrid_tui.packs import PackRegistry, PackRegistryEntry, default_packs_path, save_registry
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.palette import CommandProvider
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.screens.help_overlay import HelpOverlay
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


class _RecordingRunner:
    def __init__(self) -> None:
        self.run_calls: list[CommandEntry] = []
        self.copy_calls: list[CommandEntry] = []

    def run(self, entry: CommandEntry, *, notify_success: bool = True) -> None:
        self.run_calls.append(entry)

    def copy(self, entry: CommandEntry) -> None:
        self.copy_calls.append(entry)


async def _ready(app: LabgridTuiApp, pilot: object, n: int) -> DeviceTable:
    # Wait for rendered table rows, not just store contents: discover()/
    # search() read the live DeviceTable widget (cursor, marks), which lags
    # the store by up to one 0.2s refresh flush.
    for _ in range(80):
        await pilot.pause()  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
        table = app.screen.query_one(DeviceTable)
        if table.row_count >= n:
            return table
    raise AssertionError("table never populated")


def test_provider_registered() -> None:
    assert CommandProvider in LabgridTuiApp.COMMANDS


async def test_help_screen(fake_coordinator: tuple[FakeCoordinator, str]) -> None:
    # "?" is the dashboard's own binding, pushing the modal HelpOverlay.
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpOverlay)
        await pilot.press("question_mark")
        await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)


async def test_palette_global_reserve_hit_from_dashboard(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """GLOBAL_TEMPLATES (reservation commands) are available from any
    screen, including the dashboard."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()

        provider = CommandProvider(app.screen)
        hits = [hit async for hit in provider.search("reserve")]
        assert any("Reserve" in str(hit.match_display) for hit in hits)


async def test_my_reservation_commands_independent_of_cursor_place(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Cancel/acquire entries for the user's own reservations show up in
    the palette regardless of which (unrelated, free) place the cursor is
    on, and are absent for reservations owned by someone else."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    servicer.reservations.append(pb2.Reservation(owner=current_id(), token="TOK", state=0))
    servicer.reservations.append(pb2.Reservation(owner="host9/carol", token="OTHER", state=0))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        await app._poll_reservations()
        provider = CommandProvider(app.screen)

        names = [str(hit.display) async for hit in provider.discover()]
        assert any("Cancel reservation TOK" in name for name in names)
        assert not any("OTHER" in name for name in names)


async def test_discover_free_cursor_place_offers_acquire(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        provider = CommandProvider(app.screen)
        names = [str(hit.display) async for hit in provider.discover()]

        assert "Filter: Search places" in names
        assert "View: Toggle activity log" in names
        assert "Help: Toggle help panel" in names
        assert "Device: Acquire tb-1" in names
        assert "Device: Release tb-1" not in names
        assert "Device: Show commands for tb-1" in names
        assert "Device: Detail for tb-1" in names
        # discover() is the default list shown before typing; CLI copy
        # entries are search-only.
        assert not any(name.startswith("CLI:") for name in names)
        # No marks yet: no bulk entries.
        assert not any(name.startswith("Marks:") for name in names)
        # A free place has no resource commands to copy either way, but the
        # place-level entries (Acquire/Show/Env) stay CLI-hidden too: even
        # search() surfaces zero "CLI: ..." entries for it.
        search_names = [str(hit.match_display) async for hit in provider.search("tb-1")]
        assert not any(name.startswith("CLI:") for name in search_names)


async def test_discover_held_cursor_place_offers_release(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", acquired=current_id()))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        provider = CommandProvider(app.screen)
        names = [str(hit.display) async for hit in provider.discover()]

        assert "Device: Release tb-1" in names
        assert "Device: Acquire tb-1" not in names


async def test_search_cli_entries_flatten_evaluate_and_label_unavailable(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """A place held by someone else exposes CLI copy entries in search()
    (never in discover()), with unavailable ones still copyable but
    labeled."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1", acquired="otherhost/otheruser"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        provider = CommandProvider(app.screen)

        discovered = [str(hit.display) async for hit in provider.discover()]
        assert not any(name.startswith("CLI:") for name in discovered)

        hits = [hit async for hit in provider.search("CLI:")]
        cli_hits = {str(hit.match_display): hit for hit in hits}
        assert "CLI: Release tb-1" in cli_hits
        # Not usable by me (acquired by someone else): copyable, but flagged.
        assert cli_hits["CLI: Release tb-1"].help is not None
        assert "(unavailable)" in cli_hits["CLI: Release tb-1"].help

        runner = _RecordingRunner()
        app.runner = runner  # type: ignore[assignment]
        cli_hits["CLI: Release tb-1"].command()
        await pilot.pause()
        assert len(runner.copy_calls) == 1
        assert runner.copy_calls[0].template.label == "Release"


def _register_pack(tmp_path: Path) -> None:
    pack_file = tmp_path / "robot.toml"
    pack_file.write_text(
        "[pack]\n"
        'name = "robot"\n'
        "[[commands]]\n"
        'label = "Smoke tests"\n'
        'command = "robot -v PLACE:{place} tests/smoke"\n'
        "[[commands]]\n"
        'label = "Needs network"\n'
        'command = "robot -v IP:{res.NetworkService.address} tests/net"\n'
        'requires = ["res.NetworkService"]\n'
    )
    save_registry(
        default_packs_path(os.environ),
        PackRegistry(packs={"robot": PackRegistryEntry(name="robot", source=str(pack_file))}),
    )


async def test_search_pack_entries_appear_for_cursor_place(
    fake_coordinator: tuple[FakeCoordinator, str], tmp_path: Path
) -> None:
    """Pack entries surface in search() (never discover()) as "PACK:
    Label", copy-only, independent of held state: unlike CLI entries."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))  # free: no CLI entries, packs still show
    _register_pack(tmp_path)
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        provider = CommandProvider(app.screen)

        discovered = [str(hit.display) async for hit in provider.discover()]
        assert not any(name.startswith("robot:") for name in discovered)

        hits = [hit async for hit in provider.search("robot:")]
        pack_hits = {str(hit.match_display): hit for hit in hits}
        assert "robot: Smoke tests" in pack_hits
        assert pack_hits["robot: Smoke tests"].help == "robot -v PLACE:tb-1 tests/smoke"
        assert "robot: Needs network" in pack_hits
        assert "unavailable: needs NetworkService" in pack_hits["robot: Needs network"].help

        runner = _RecordingRunner()
        app.runner = runner  # type: ignore[assignment]
        pack_hits["robot: Smoke tests"].command()
        await pilot.pause()
        assert len(runner.copy_calls) == 1
        assert runner.copy_calls[0].template.category == "robot"
        assert runner.copy_calls[0].template.copy_only is True
        assert runner.run_calls == []


async def test_discover_bulk_entries_appear_only_with_marks(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-a"))
    servicer.places.append(pb2.Place(name="tb-b"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 2)
        provider = CommandProvider(app.screen)

        names = [str(hit.display) async for hit in provider.discover()]
        assert not any(name.startswith("Marks:") for name in names)

        table.marks.update({"tb-a", "tb-b"})
        names = [str(hit.display) async for hit in provider.discover()]
        assert "Marks: Acquire all (2 marked)" in names
        assert "Marks: Release all (2 marked)" in names


@pytest.mark.parametrize("key", [":", "ctrl+p"])
async def test_palette_keys_open_command_palette_from_dashboard(
    fake_coordinator: tuple[FakeCoordinator, str], key: str
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not isinstance(app.screen, CommandPalette)
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.parametrize("key", [":", "ctrl+p"])
async def test_palette_keys_open_command_palette_from_detail_overlay(
    fake_coordinator: tuple[FakeCoordinator, str], key: str
) -> None:
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("d")
        await pilot.pause()
        assert isinstance(app.screen, DetailOverlay)
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.parametrize("key", [":", "ctrl+p"])
async def test_palette_keys_open_command_palette_from_help_overlay(
    fake_coordinator: tuple[FakeCoordinator, str], key: str
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("question_mark")
        await pilot.pause()
        assert isinstance(app.screen, HelpOverlay)
        await pilot.press(key)
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


async def test_colon_is_a_filter_character_inside_command_overlay(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Unlike every other screen, ':' must not open the palette here: it
    is a printable character forwarded to the filter input."""
    from textual.widgets import Input

    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        await pilot.press(":")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        assert app.screen.query_one("#overlay-filter", Input).value == ":"


async def test_ctrl_n_ctrl_p_move_command_overlay_list_cursor(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """ctrl+n/ctrl+p are reassigned inside the command overlay (fzf/vim
    completion keys), taking priority over the global palette binding --
    they must move the option list cursor whether the filter Input or the
    OptionList itself holds focus, and typing must keep filtering
    afterward."""
    from textual.widgets import Input, OptionList

    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await _ready(app, pilot, 1)
        app.screen.query_one(DeviceTable).focus()
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)

        # The Info tab has two entries (Device info, Export env) regardless
        # of the place's resources, unlike Manage's single Acquire entry.
        await pilot.press("tab")
        await pilot.pause()
        options = app.screen.query_one("#overlay-list-info", OptionList)
        assert options.option_count == 2
        assert options.highlighted == 0

        await pilot.press("ctrl+n")
        await pilot.pause()
        assert options.highlighted == 1
        assert not isinstance(app.screen, CommandPalette)

        await pilot.press("ctrl+p")
        await pilot.pause()
        assert options.highlighted == 0
        assert not isinstance(app.screen, CommandPalette)

        # Explicitly focus the filter input: ctrl+n/ctrl+p still drive the
        # list, not the input, regardless of what holds focus.
        app.screen.query_one("#overlay-filter", Input).focus()
        await pilot.press("ctrl+n")
        await pilot.pause()
        assert options.highlighted == 1

        await pilot.press("d", "e", "v")
        await pilot.pause()
        assert app.screen.query_one("#overlay-filter", Input).value == "dev"


async def test_ctrl_p_renders_discover_entries_in_real_palette(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Unlike the provider-level tests above (which call discover()/search()
    directly), this drives the real key binding and asserts against
    Textual's own CommandPalette widget: the discover() entries must
    actually reach its rendered OptionList, not just CommandProvider's
    async generator."""
    servicer, address = fake_coordinator
    servicer.places.append(pb2.Place(name="tb-1"))
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        table = await _ready(app, pilot, 1)
        table.focus()
        await pilot.press("ctrl+p")

        command_list: CommandList | None = None
        for _ in range(60):
            await pilot.pause()
            await asyncio.sleep(0.05)
            if isinstance(app.screen, CommandPalette):
                try:
                    command_list = app.screen.query_one(CommandList)
                except Exception:
                    continue
                if command_list.option_count:
                    break
        assert isinstance(app.screen, CommandPalette)
        assert command_list is not None and command_list.option_count

        prompts = [
            str(command_list.get_option_at_index(i).prompt)
            for i in range(command_list.option_count)
        ]
        assert any("Filter: Search places" in prompt for prompt in prompts)
        assert any("Coordinator: manage" in prompt for prompt in prompts)
        assert any("Device: Acquire tb-1" in prompt for prompt in prompts)

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, CommandPalette)
        assert app.focused is app.screen.query_one(DeviceTable)


async def test_palette_copy_goes_through_action_runner(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    """Every palette copy, global reservation templates included, routes
    through the app's ActionRunner so it toasts and logs like every other
    copy path."""
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test() as pilot:
        await pilot.pause()
        provider = CommandProvider(app.screen)
        runner = _RecordingRunner()
        app.runner = runner  # type: ignore[assignment]

        hits = [hit async for hit in provider.search("Reserve")]
        reserve_hit = next(h for h in hits if str(h.match_display).startswith("Reserve "))
        reserve_hit.command()
        await pilot.pause()
        assert len(runner.copy_calls) == 1
        assert runner.copy_calls[0].template.label == "Reserve"
