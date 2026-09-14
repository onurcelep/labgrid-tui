from textual.app import App, ComposeResult

from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.help_text import (
    TAB_KEY_BINDINGS,
    TAB_OPERATIONS,
    TAB_TABLE_REFERENCE,
)

_ALL_HELP_TABS = TAB_KEY_BINDINGS + TAB_OPERATIONS + TAB_TABLE_REFERENCE


class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield ActivityLog()


async def test_activity_log_lines() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        log = app.query_one(ActivityLog)
        log.log_line("hello")
        await pilot.pause()
        assert log.lines  # rendered strip exists


def test_help_text_covers_new_keys() -> None:
    for key in ("shift+r", "space", "ctrl+a", "power commands", "acquire"):
        assert key in _ALL_HELP_TABS
    assert "labgrid-client -p PLACE acquire" in TAB_OPERATIONS  # primer kept
    assert "| `SER` | console |" in TAB_TABLE_REFERENCE  # chip key reference


def test_help_text_covers_shift_enter_copy_via_select() -> None:
    assert "shift+enter" in TAB_KEY_BINDINGS
    assert "copy via select" in TAB_KEY_BINDINGS
