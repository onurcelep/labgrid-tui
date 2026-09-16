"""The tour step card's placement: pure geometry, no app required.

Keeping ``choose_card_placement`` a plain function is what makes every
edge case here assertable without laying out a screen; the walkthrough in
test_tour.py then checks that the app really paints the card where this
function put it.
"""

import pytest
from textual.geometry import Region, Size

from labgrid_tui.ui.widgets.tour_card import (
    CARD_WIDTH,
    card_width,
    centered_offset,
    choose_card_placement,
)

SCREEN = Size(80, 24)
CARD = Size(44, 5)


def _place(target: Region, card: Size = CARD, screen: Size = SCREEN) -> tuple[Region, str]:
    offset, side = choose_card_placement(target, card, screen)
    return Region(offset.x, offset.y, card.width, card.height), side


def _in_bounds(region: Region, screen: Size) -> bool:
    return (
        region.x >= 0
        and region.y >= 0
        and region.right <= screen.width
        and region.bottom <= screen.height
    )


def test_below_is_preferred_and_aligns_with_the_target_left_edge() -> None:
    target = Region(2, 3, 20, 1)
    region, side = _place(target)
    assert side == "below"
    assert region.y == target.bottom
    assert region.x == target.x


def test_falls_back_above_when_there_is_no_room_below() -> None:
    target = Region(0, 20, 30, 1)  # only three rows left under it
    region, side = _place(target)
    assert side == "above"
    assert region.bottom == target.y
    assert region.x == target.x


def test_falls_back_right_when_the_target_spans_the_full_height() -> None:
    target = Region(10, 0, 20, 24)
    region, side = _place(target)
    assert side == "right"
    assert region.x == target.right
    assert region.y == target.y


def test_falls_back_left_when_the_right_is_off_screen() -> None:
    target = Region(46, 0, 30, 24)  # right edge at 76: no room for 44
    region, side = _place(target)
    assert side == "left"
    assert region.right == target.x


@pytest.mark.parametrize(
    "target",
    [
        Region(0, 0, 1, 1),
        Region(79, 0, 1, 1),
        Region(0, 23, 1, 1),
        Region(79, 23, 1, 1),
        Region(0, 0, 80, 1),
        Region(0, 23, 80, 1),
        Region(70, 10, 10, 3),
        Region(0, 10, 1, 3),
    ],
)
def test_clamped_into_the_screen_at_every_edge(target: Region) -> None:
    region, _side = _place(target)
    assert _in_bounds(region, SCREEN)
    assert not region.overlaps(target)


def test_stays_in_bounds_on_the_smallest_screen() -> None:
    screen = Size(40, 12)
    card = Size(card_width(screen), 6)
    for target in (Region(0, 0, 12, 1), Region(28, 11, 12, 1), Region(0, 5, 40, 1)):
        region, _side = _place(target, card, screen)
        assert _in_bounds(region, screen)
        assert not region.overlaps(target)


def test_a_target_the_size_of_the_screen_stays_in_bounds() -> None:
    # Nothing can be adjacent to it, so the card overlaps rather than
    # disappearing off screen; being visible is what matters here.
    region, side = _place(Region(0, 0, 80, 24))
    assert side == "below"
    assert _in_bounds(region, SCREEN)


def test_the_card_narrows_with_the_terminal() -> None:
    assert card_width(Size(200, 50)) == CARD_WIDTH
    assert card_width(Size(80, 24)) == CARD_WIDTH
    assert card_width(Size(40, 12)) == 38
    assert card_width(Size(3, 3)) == 1


def test_a_step_about_nothing_puts_the_card_in_the_middle() -> None:
    offset = centered_offset(CARD, SCREEN)
    assert offset.x == (SCREEN.width - CARD.width) // 2
    assert offset.y == (SCREEN.height - CARD.height) // 2
    assert centered_offset(Size(100, 40), SCREEN) == (0, 0)
