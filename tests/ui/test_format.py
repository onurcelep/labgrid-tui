from rich.text import Text

from labgrid_tui.ui.format import (
    TagLayout,
    TagSlot,
    abbrev,
    capability_chips,
    fit_layout,
    format_age,
    format_tags,
    tag_layout,
)


def test_abbrev_known_and_fallback() -> None:
    assert abbrev("console") == "SER"
    assert abbrev("power") == "PWR"
    assert abbrev("video") == "CAM"
    assert abbrev("magic") == "MAGIC"


def test_chips_colors() -> None:
    chips = capability_chips({"console", "ssh"}, {"power"})
    assert isinstance(chips, Text)
    assert chips.plain == "SER PWR SSH"  # sorted by capability name
    styles = {chips.plain[span.start : span.end]: str(span.style) for span in chips.spans}
    assert styles["PWR"] == "red"
    assert styles["SER"] == "green"
    assert styles["SSH"] == "green"


def test_chips_unknown_and_empty() -> None:
    assert capability_chips(set(), set()) == "-"
    only_unknown = capability_chips(set(), set(), unknown=True)
    assert isinstance(only_unknown, Text)
    assert only_unknown.plain == "?"


def test_format_age() -> None:
    assert format_age(-1) == "-"
    assert format_age(30) == "30s ago"
    assert format_age(120) == "2m ago"
    assert format_age(3 * 3600) == "3h ago"
    assert format_age(5 * 86400 + 7) == "5d ago"


def _dimmed(text: Text) -> set[str]:
    return {text.plain[span.start : span.end] for span in text.spans if "dim" in str(span.style)}


def test_tag_layout_orders_by_coverage_then_key() -> None:
    layout = tag_layout(
        [
            {"site": "hall-a", "rack": "r2"},
            {"site": "hall-b", "rack": "r11", "owner": "ci"},
            {"site": "hall-b"},
        ]
    )
    assert [slot.key for slot in layout.slots] == ["site", "rack", "owner"]
    # Widest key=value the fleet carries, per key.
    assert [slot.width for slot in layout.slots] == [len("site=hall-a"), len("rack=r11"), 8]
    assert layout.total_width == 11 + 8 + 8 + 2


def test_tag_layout_ties_on_coverage_are_alphabetical() -> None:
    layout = tag_layout([{"site": "hall-a", "owner": "ci"}, {"site": "hall-b", "owner": "qa"}])
    assert [slot.key for slot in layout.slots] == ["owner", "site"]


def test_tag_layout_constant_keys_need_every_place_and_one_value() -> None:
    layout = tag_layout(
        [
            {"site": "hall-a", "owner": "ci", "rack": "r2"},
            {"site": "hall-a", "owner": "qa"},
        ]
    )
    constant = {slot.key for slot in layout.slots if slot.constant}
    # site: on every place, one value. owner: everywhere, two values.
    # rack: one value, but a place is missing it.
    assert constant == {"site"}


def test_tag_layout_of_an_untagged_fleet_is_empty() -> None:
    layout = tag_layout([{}, {}])
    assert layout.slots == ()
    assert layout.total_width == 0


def test_format_tags_pads_pairs_into_their_slots() -> None:
    layout = tag_layout([{"board": "imx8", "env": "dev"}, {"board": "stm32mp1", "env": "staging"}])
    first = format_tags({"board": "imx8", "env": "dev"}, layout)
    second = format_tags({"board": "stm32mp1", "env": "staging"}, layout)
    assert isinstance(first, Text) and isinstance(second, Text)
    # The env pair starts at the same column on both rows.
    assert first.plain == "board=imx8     env=dev"
    assert second.plain == "board=stm32mp1 env=staging"
    assert first.plain.index("env=") == second.plain.index("env=")
    assert _dimmed(first) == {"board=", "env="}


def test_format_tags_leaves_a_blank_slot_for_a_missing_key() -> None:
    layout = tag_layout([{"site": "hall-a", "rack": "r2"}, {"rack": "r11"}])
    with_site = format_tags({"site": "hall-a", "rack": "r2"}, layout)
    without = format_tags({"rack": "r11"}, layout)
    assert isinstance(with_site, Text) and isinstance(without, Text)
    assert [slot.key for slot in layout.slots] == ["rack", "site"]
    assert with_site.plain == "rack=r2  site=hall-a"
    assert without.plain == "rack=r11"


def test_format_tags_dims_a_constant_pair_whole() -> None:
    layout = tag_layout([{"site": "hall-a", "owner": "ci"}, {"site": "hall-a", "owner": "qa"}])
    tags = format_tags({"site": "hall-a", "owner": "ci"}, layout)
    assert isinstance(tags, Text)
    # owner is not constant: only its key is dim, the value stands out.
    # site is the same on every place: the whole pair recedes.
    assert _dimmed(tags) == {"owner=", "site=hall-a"}


def test_format_tags_empty_is_a_dash() -> None:
    assert format_tags({}, tag_layout([{"site": "hall-a"}])) == "-"


def test_fit_layout_drops_constant_slots_from_the_right() -> None:
    layout = tag_layout(
        [
            {"owner": "ci", "rack": "r2", "site": "hall-a"},
            {"owner": "qa", "rack": "r2", "site": "hall-a"},
        ]
    )
    assert [slot.key for slot in layout.slots] == ["owner", "rack", "site"]
    assert {slot.key for slot in layout.slots if slot.constant} == {"rack", "site"}
    assert fit_layout(layout, layout.total_width).slots == layout.slots
    # site goes first, then rack; owner tells the rows apart and stays.
    assert [slot.key for slot in fit_layout(layout, 17).slots] == ["owner", "rack"]
    assert [slot.key for slot in fit_layout(layout, 8).slots] == ["owner"]
    # Nothing constant is left to give: the last slot survives to be cropped.
    assert [slot.key for slot in fit_layout(layout, 2).slots] == ["owner"]


def test_fit_layout_keeps_rows_aligned_after_the_cut() -> None:
    places = [
        {"owner": "ci", "site": "hall-a", "rack": "r2"},
        {"owner": "qa-bot", "site": "hall-a"},
    ]
    layout = tag_layout(places)
    # site is constant and goes first; rack tells the places apart and stays.
    fitted = fit_layout(layout, 18)
    assert [slot.key for slot in fitted.slots] == ["owner", "rack"]
    rows = [format_tags(tags, fitted, width=18) for tags in places]
    assert [str(row) for row in rows] == ["owner=ci     rack=", "owner=qa-bot"]
    # Every row dropped the same slot, so what is left is still in column.
    assert fitted.slots[0].width == len("owner=qa-bot")


def test_format_tags_cut_to_width_without_an_ellipsis() -> None:
    layout = tag_layout([{"board": "imx8", "env": "dev"}])
    tags = format_tags({"board": "imx8", "env": "dev"}, layout, width=12)
    assert isinstance(tags, Text)
    assert tags.plain == "board=imx8 e"
    assert "\u2026" not in tags.plain
    # A cut keeps the styling of what survives: the first key is still dim.
    assert any("dim" in str(span.style) for span in tags.spans)


def test_format_tags_width_wider_than_the_pairs_changes_nothing() -> None:
    layout = tag_layout([{"board": "imx8"}])
    tags = format_tags({"board": "imx8"}, layout, width=40)
    assert isinstance(tags, Text)
    assert tags.plain == "board=imx8"


def test_format_tags_ignores_keys_the_layout_does_not_carry() -> None:
    """A fitted layout is a subset of the fleet's keys; a place's pair in a
    dropped slot is simply not rendered, never appended out of column."""
    layout = TagLayout((TagSlot(key="owner", width=8, constant=False),))
    tags = format_tags({"owner": "ci", "site": "hall-a"}, layout)
    assert isinstance(tags, Text)
    assert tags.plain == "owner=ci"
