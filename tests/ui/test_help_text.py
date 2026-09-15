"""Tests for the per-tab help content (status dots, table columns,
operations prose, activity-log legend) and its sync with the README Keys
table."""

from pathlib import Path

from labgrid_tui.ui.widgets.help_text import (
    TAB_ACTIVITY_LOG,
    TAB_OPERATIONS,
    TAB_TABLE_REFERENCE,
)

_README = Path(__file__).resolve().parents[2] / "README.md"


def test_help_text_covers_status_dot_legend() -> None:
    for word in ("status dots", "free", "reserved", "acquired", "offline", "unknown"):
        assert word in TAB_TABLE_REFERENCE


def test_help_text_covers_table_columns() -> None:
    for word in ("table columns", "capabilities", "changed", "comment", "tag columns"):
        assert word in TAB_TABLE_REFERENCE


def test_help_text_covers_operations_prose() -> None:
    for phrase in ("mark then verb", "filter", "get a bench"):
        assert phrase in TAB_OPERATIONS


def test_help_text_covers_activity_log_legend() -> None:
    assert "activity log events" in TAB_ACTIVITY_LOG
    for icon in ("▶", "■", "▲", "▼", "✖", "◆"):
        assert icon in TAB_ACTIVITY_LOG
    assert "reservation" in TAB_ACTIVITY_LOG
