"""Unit tests for the responsive-layout helpers (breakpoints, sizing)."""

import pytest
from textual.geometry import Size

from labgrid_tui.ui.layout import (
    HORIZONTAL_BREAKPOINTS,
    MIN_HEIGHT,
    MIN_WIDTH,
    VERTICAL_BREAKPOINTS,
    is_narrow,
    is_short,
    middle_ellipsis,
    too_small,
)


def test_horizontal_breakpoints_cover_narrow_normal_wide() -> None:
    names = dict(HORIZONTAL_BREAKPOINTS)
    assert names[0] == "-narrow"
    assert names[80] == "-normal"
    assert names[120] == "-wide"


def test_vertical_breakpoints_cover_short() -> None:
    names = dict(VERTICAL_BREAKPOINTS)
    assert names[0] == "-short"


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        (Size(59, 12), True),
        (Size(60, 11), True),
        (Size(60, 12), False),
        (Size(200, 60), False),
    ],
)
def test_too_small(size: Size, expected: bool) -> None:
    assert too_small(size) is expected
    assert (size.width < MIN_WIDTH or size.height < MIN_HEIGHT) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [(Size(79, 24), True), (Size(80, 24), False), (Size(200, 24), False)],
)
def test_is_narrow(size: Size, expected: bool) -> None:
    assert is_narrow(size) is expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [(Size(80, 19), True), (Size(80, 20), False), (Size(80, 60), False)],
)
def test_is_short(size: Size, expected: bool) -> None:
    assert is_short(size) is expected


def test_middle_ellipsis_short_text_unchanged() -> None:
    assert middle_ellipsis("tb-a", 20) == "tb-a"


def test_middle_ellipsis_keeps_head_and_tail() -> None:
    result = middle_ellipsis("bench-14-bench-14-06", 16)
    assert len(result) == 16
    assert result.startswith("bench-1")
    assert result.endswith("14-06")
    assert "…" in result


def test_middle_ellipsis_degenerate_widths() -> None:
    assert middle_ellipsis("hello", 0) == ""
    assert middle_ellipsis("hello", 1) == "…"


def test_middle_ellipsis_exact_fit() -> None:
    assert middle_ellipsis("12345", 5) == "12345"
