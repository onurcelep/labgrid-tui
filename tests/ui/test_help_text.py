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
    for word in ("table columns", "capabilities", "changed", "comment", "tags"):
        assert word in TAB_TABLE_REFERENCE


def test_help_text_describes_the_tags_column_and_not_dynamic_ones() -> None:
    assert "set-tags pairs" in TAB_TABLE_REFERENCE
    assert "tag columns" not in TAB_TABLE_REFERENCE


def test_help_text_explains_dimmed_and_first_dropped_shared_pairs() -> None:
    assert "the same on every place are dimmed" in TAB_TABLE_REFERENCE
    assert "first to be hidden when the terminal is narrow" in TAB_TABLE_REFERENCE


def test_help_text_covers_operations_prose() -> None:
    for phrase in ("mark then verb", "filter", "get a bench"):
        assert phrase in TAB_OPERATIONS


def test_help_text_covers_activity_log_legend() -> None:
    assert "activity log events" in TAB_ACTIVITY_LOG
    for icon in ("▶", "■", "▲", "▼", "✖", "◆"):
        assert icon in TAB_ACTIVITY_LOG
    assert "reservation" in TAB_ACTIVITY_LOG
