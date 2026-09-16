"""Tests for the per-tab help content (status dots, table columns,
operations prose, activity-log legend) and its sync with the README Keys
table."""

from pathlib import Path

from labgrid_tui.model.capabilities import DEFAULT_CAPABILITIES
from labgrid_tui.ui.format import abbrev
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
    for word in ("table columns", "resources", "changed", "comment", "tags"):
        assert word in TAB_TABLE_REFERENCE


def test_chip_legend_is_generated_from_the_class_mapping() -> None:
    """Every resource class the app knows has a row, chip included: the
    legend is derived from the mapping, so adding a class there cannot
    leave the help behind."""
    assert "resource chips" in TAB_TABLE_REFERENCE
    for cls, capability in DEFAULT_CAPABILITIES.items():
        assert f"| `{cls}` | `{abbrev(capability)}` |" in TAB_TABLE_REFERENCE
    # Spot checks for the vocabulary the column header promises.
    assert "| `NetworkSerialPort` | `SER` |" in TAB_TABLE_REFERENCE
    assert "| `NetworkPowerPort` | `PWR` |" in TAB_TABLE_REFERENCE
    assert "| `NetworkService` | `SSH` |" in TAB_TABLE_REFERENCE


def test_help_text_describes_the_tags_column_and_not_dynamic_ones() -> None:
    assert "set-tags pairs" in TAB_TABLE_REFERENCE
    assert "tag columns" not in TAB_TABLE_REFERENCE


def test_help_text_explains_dimmed_and_first_dropped_shared_pairs() -> None:
    # Source wrapping is not part of the promise; Textual rewraps this to
    # the pane width anyway.
    prose = " ".join(TAB_TABLE_REFERENCE.split())
    assert "the same on every place are dimmed" in prose
    assert "first to be hidden when the terminal is narrow" in prose


def test_help_text_explains_that_aliases_go_first_under_width_pressure() -> None:
    prose = " ".join(TAB_TABLE_REFERENCE.split())
    assert "aliases are the first thing width pressure takes" in prose


def test_help_text_lists_the_detail_fields_in_labgrid_order() -> None:
    """The overlay mirrors Place.show(); the legend has to name the same
    fields in the same order or it stops being a map of the screen."""
    prose = " ".join(TAB_TABLE_REFERENCE.split())
    order = (
        "aliases, comment, tags, matches, acquired (the full `host/user`), "
        "acquired resources, allowed, created, changed, reservation"
    )
    assert order in prose
    # State is this app's own reading, not one of show()'s fields, so the
    # legend has to place it outside that list.
    assert "State first" in prose
    assert prose.index("State first") < prose.index(order)


def test_help_text_covers_operations_prose() -> None:
    for phrase in ("mark then verb", "filter", "get a bench"):
        assert phrase in TAB_OPERATIONS


def test_help_text_covers_activity_log_legend() -> None:
    assert "activity log events" in TAB_ACTIVITY_LOG
    for icon in ("▶", "■", "▲", "▼", "✖", "◆"):
        assert icon in TAB_ACTIVITY_LOG
    assert "reservation" in TAB_ACTIVITY_LOG
