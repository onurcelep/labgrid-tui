"""Tests for the ActivityLog line-formatting vocabulary: dim timestamp
prefix, per-kind icon, bold subject, per-kind colored detail."""

from rich.text import Text
from textual.app import App, ComposeResult

from labgrid_tui.model.events import (
    KIND_ACQUIRED,
    KIND_DELETED,
    KIND_ERROR,
    KIND_NEUTRAL,
    KIND_RELEASED,
    KIND_RESERVATION,
    KIND_RESOURCE_DELETED,
    KIND_RESOURCE_OFFLINE,
    KIND_RESOURCE_ONLINE,
)
from labgrid_tui.ui.widgets.activity_log import (
    _KIND_ICONS,
    _KIND_STYLES,
    ActivityLog,
    format_line,
)

_COORDINATOR_KINDS = (
    KIND_ACQUIRED,
    KIND_RELEASED,
    KIND_DELETED,
    KIND_RESERVATION,
    KIND_RESOURCE_ONLINE,
    KIND_RESOURCE_OFFLINE,
    KIND_RESOURCE_DELETED,
)


def test_format_line_returns_text() -> None:
    assert isinstance(format_line(KIND_ACQUIRED, "tb-1", "acquired by alice"), Text)


def test_format_line_has_hh_mm_ss_prefix() -> None:
    result = format_line(KIND_NEUTRAL, "", "hello")
    prefix = result.plain[:8]
    assert len(prefix) == 8 and prefix[2] == ":" and prefix[5] == ":"


def test_format_line_timestamp_is_dim() -> None:
    result = format_line(KIND_NEUTRAL, "", "hello")
    dim_spans = [s for s in result.spans if s.style == "dim"]
    assert dim_spans and dim_spans[0].start == 0 and dim_spans[0].end >= 8


def test_format_line_contains_subject_and_detail() -> None:
    result = format_line(KIND_ACQUIRED, "tb-1", "acquired by alice")
    assert "tb-1" in result.plain
    assert "acquired by alice" in result.plain


def test_format_line_bolds_subject() -> None:
    result = format_line(KIND_ACQUIRED, "tb-1", "acquired by alice")
    start = result.plain.index("tb-1")
    end = start + len("tb-1")
    assert any(s.style == "bold" and s.start <= start and s.end >= end for s in result.spans)


def test_format_line_empty_subject_has_no_bold_span() -> None:
    result = format_line(KIND_NEUTRAL, "", "$ labgrid-client -p tb-1 acquire")
    assert not any(s.style == "bold" for s in result.spans)


def test_format_line_neutral_has_no_icon() -> None:
    result = format_line(KIND_NEUTRAL, "", "$ labgrid-client -p tb-1 acquire")
    for icon in _KIND_ICONS.values():
        if icon:
            assert icon not in result.plain


def test_every_coordinator_kind_has_icon_and_style() -> None:
    for kind in _COORDINATOR_KINDS:
        assert _KIND_ICONS[kind]
        assert _KIND_STYLES[kind]


def test_format_line_uses_kind_icon() -> None:
    for kind, icon in _KIND_ICONS.items():
        if not icon:
            continue
        result = format_line(kind, "subject", "detail")
        assert icon in result.plain


def test_format_line_applies_kind_style_to_detail() -> None:
    result = format_line(KIND_DELETED, "tb-1", "removed")
    style = _KIND_STYLES[KIND_DELETED]
    assert any(s.style == style for s in result.spans)


def test_error_kind_is_red() -> None:
    assert _KIND_STYLES[KIND_ERROR] == "red"
    result = format_line(KIND_ERROR, "", "[exit 2] labgrid-client -p tb-1 acquire")
    assert any(s.style == "red" for s in result.spans)


def test_resource_deleted_uses_dim_red_matching_place_deleted_icon() -> None:
    # Both "kinds of deletion" share the same heavy-X glyph; resource
    # deletion is the dimmer of the two.
    assert _KIND_ICONS[KIND_RESOURCE_DELETED] == _KIND_ICONS[KIND_DELETED]
    assert _KIND_STYLES[KIND_RESOURCE_DELETED] == "red dim"
    assert _KIND_STYLES[KIND_DELETED] == "red"


class _Harness(App[None]):
    def compose(self) -> ComposeResult:
        yield ActivityLog()


async def test_log_line_renders_with_timestamp() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        log = app.query_one(ActivityLog)
        log.log_line("hello")
        await pilot.pause()
        text = "\n".join(strip.text for strip in log.lines)
        assert "hello" in text


async def test_log_event_renders_icon_and_subject() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        log = app.query_one(ActivityLog)
        log.log_event(KIND_ACQUIRED, "tb-1", "acquired by alice")
        await pilot.pause()
        text = "\n".join(strip.text for strip in log.lines)
        assert "▶" in text
        assert "tb-1" in text
        assert "acquired by alice" in text


async def test_log_output_has_no_timestamp_prefix() -> None:
    # log_output carries already-formatted external process output, not an
    # activity message: stamping every line of multi-line command output
    # with its own HH:MM:SS prefix would be a fidelity regression.
    app = _Harness()
    async with app.run_test() as pilot:
        log = app.query_one(ActivityLog)
        log.log_output("plain stdout line")
        await pilot.pause()
        text = "\n".join(strip.text for strip in log.lines)
        assert text.strip() == "plain stdout line"
