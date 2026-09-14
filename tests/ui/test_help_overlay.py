from textual.containers import VerticalScroll
from textual.widgets import TabbedContent

from labgrid_tui.config import Config
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.help_overlay import HelpOverlay
from labgrid_tui.ui.widgets.device_table import DeviceTable
from tests.fake_coordinator import FakeCoordinator


def _config(address: str) -> Config:
    return Config(coordinator=address, coordinator_source="flag", prefix=None,
                  capability_overrides={}, proxy_set=False)


async def _open_help(app: LabgridTuiApp, pilot: object) -> HelpOverlay:
    app.screen.query_one(DeviceTable).focus()  # type: ignore[attr-defined]
    await pilot.press("question_mark")  # type: ignore[attr-defined]
    await pilot.pause()  # type: ignore[attr-defined]
    assert isinstance(app.screen, HelpOverlay)
    return app.screen


async def test_question_mark_opens_modal_with_focus_on_active_pane(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        overlay = await _open_help(app, pilot)
        tabbed = overlay.query_one(TabbedContent)
        pane = tabbed.active_pane
        assert pane is not None
        scroll = pane.query_one(VerticalScroll)
        assert app.focused is scroll


async def test_right_switches_tab_and_focuses_new_pane(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        overlay = await _open_help(app, pilot)
        tabbed = overlay.query_one(TabbedContent)
        first_active = tabbed.active

        await pilot.press("right")
        await pilot.pause()

        assert tabbed.active != first_active
        pane = tabbed.active_pane
        assert pane is not None
        scroll = pane.query_one(VerticalScroll)
        assert app.focused is scroll


async def test_h_l_switch_tabs_like_left_right(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        overlay = await _open_help(app, pilot)
        tabbed = overlay.query_one(TabbedContent)
        first_active = tabbed.active

        await pilot.press("l")
        await pilot.pause()
        assert tabbed.active != first_active
        pane = tabbed.active_pane
        assert pane is not None
        scroll = pane.query_one(VerticalScroll)
        assert app.focused is scroll

        await pilot.press("h")
        await pilot.pause()
        assert tabbed.active == first_active


async def test_scroll_active_pane_without_tab(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        overlay = await _open_help(app, pilot)
        tabbed = overlay.query_one(TabbedContent)
        pane = tabbed.active_pane
        assert pane is not None
        scroll = pane.query_one(VerticalScroll)

        assert scroll.scroll_y == 0
        await pilot.press("j")
        await pilot.pause()
        assert scroll.scroll_y > 0
        after_j = scroll.scroll_y

        await pilot.press("pagedown")
        await pilot.pause()
        assert scroll.scroll_y >= after_j

        await pilot.press("G")
        await pilot.pause()
        max_y = scroll.scroll_y

        await pilot.press("g")
        await pilot.pause()
        assert scroll.scroll_y == 0
        assert max_y >= 0


async def test_escape_closes_and_restores_table_focus(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        await _open_help(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)
        table = app.screen.query_one(DeviceTable)
        assert app.focused is table


async def test_q_closes_and_restores_table_focus(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        await _open_help(app, pilot)
        await pilot.press("q")
        await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)
        table = app.screen.query_one(DeviceTable)
        assert app.focused is table


async def test_question_mark_closes_and_restores_table_focus(
    fake_coordinator: tuple[FakeCoordinator, str],
) -> None:
    _, address = fake_coordinator
    app = LabgridTuiApp(_config(address))
    async with app.run_test(size=(100, 24)) as pilot:
        await _open_help(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()
        assert not isinstance(app.screen, HelpOverlay)
        table = app.screen.query_one(DeviceTable)
        assert app.focused is table
