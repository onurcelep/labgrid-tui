from rich.text import Text

from labgrid_tui.coordinator.models import Place
from labgrid_tui.ui.format import (
    abbrev,
    capability_chips,
    format_age,
    top_tag_keys,
)


def _place(name: str, tags: dict[str, str]) -> Place:
    return Place(name=name, aliases=(), comment="", tags=tags, matches=(),
                 acquired=None, acquired_resources=(), allowed=(),
                 created=0.0, changed=0.0, reservation=None)


def test_abbrev_known_and_fallback() -> None:
    assert abbrev("console") == "SER"
    assert abbrev("power") == "PWR"
    assert abbrev("video") == "CAM"
    assert abbrev("magic") == "MAGIC"


def test_chips_colors() -> None:
    chips = capability_chips({"console", "ssh"}, {"power"})
    assert isinstance(chips, Text)
    assert chips.plain == "SER PWR SSH"  # sorted by capability name
    styles = {chips.plain[span.start:span.end]: str(span.style) for span in chips.spans}
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


def test_top_tag_keys_frequency_then_name() -> None:
    places = [
        _place("a", {"env": "dev", "site": "x"}),
        _place("b", {"env": "dev", "site": "y", "gateway": "g"}),
        _place("c", {"env": "prod", "board": "imx8"}),
    ]
    assert top_tag_keys(places, limit=3) == ["env", "site", "board"]
    assert top_tag_keys([], limit=3) == []


