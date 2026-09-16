from rich.text import Text

from labgrid_tui.ui.format import (
    abbrev,
    capability_chips,
    format_age,
    format_tags,
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


def test_format_tags_sorted_pairs_with_dimmed_keys() -> None:
    tags = format_tags({"site": "lab1", "board": "imx8", "env": "dev"})
    assert isinstance(tags, Text)
    assert tags.plain == "board=imx8 env=dev site=lab1"
    dimmed = {tags.plain[span.start : span.end] for span in tags.spans if "dim" in str(span.style)}
    assert dimmed == {"board=", "env=", "site="}


def test_format_tags_empty_is_a_dash() -> None:
    assert format_tags({}) == "-"


def test_format_tags_cut_to_width_without_an_ellipsis() -> None:
    tags = format_tags({"board": "imx8", "env": "dev"}, width=12)
    assert isinstance(tags, Text)
    assert tags.plain == "board=imx8 e"
    # A cut keeps the styling of what survives: the first key is still dim.
    assert any("dim" in str(span.style) for span in tags.spans)


def test_format_tags_width_wider_than_the_pairs_changes_nothing() -> None:
    tags = format_tags({"board": "imx8"}, width=40)
    assert isinstance(tags, Text)
    assert tags.plain == "board=imx8"
