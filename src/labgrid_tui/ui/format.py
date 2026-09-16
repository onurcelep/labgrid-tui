"""Presentation helpers: capability chips, humanized ages, tag pairs.

The rendering vocabulary (colored capability abbreviations, status dots,
"5d ago" timestamps) stays generic labgrid: nothing lab-specific.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from rich.text import Text

CAPABILITY_ABBREV: dict[str, str] = {
    "console": "SER",
    "power": "PWR",
    "ssh": "SSH",
    "io": "IO",
    "sdmux": "SDMUX",
    "usbmux": "USBMUX",
    "video": "CAM",
    "audio": "AUD",
    "storage": "STOR",
    "flash": "FLASH",
    "dfu": "DFU",
    "fastboot": "FB",
    "bootstrap": "BOOT",
    "tmc": "TMC",
    "debug": "DBG",
    "measure": "MEAS",
}


def abbrev(capability: str) -> str:
    return CAPABILITY_ABBREV.get(capability, capability.upper())


def capability_chips(online: set[str], offline: set[str], *, unknown: bool = False) -> Text | str:
    """Colored abbreviation chips: green online, red offline, yellow ?."""
    caps = sorted(online | offline)
    if not caps and not unknown:
        return "-"
    text = Text()
    for i, cap in enumerate(caps):
        if i:
            text.append(" ")
        text.append(abbrev(cap), style="green" if cap in online else "red")
    if unknown:
        if caps:
            text.append(" ")
        text.append("?", style="yellow")
    return text


def format_age(seconds: float) -> str:
    if seconds < 0:
        return "-"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


@dataclass(frozen=True)
class TagSlot:
    """One key's fixed column inside the Tags cell.

    *width* is the widest ``key=value`` the fleet carries for this key, so
    every row reserves the same room and the pairs line up vertically.
    *constant* marks a key every place has with one and the same value:
    it separates no place from any other, so it is dimmed and is the first
    thing dropped when the cell has to be cut.
    """

    key: str
    width: int
    constant: bool


@dataclass(frozen=True)
class TagLayout:
    """Fleet-wide slot order for the Tags column."""

    slots: tuple[TagSlot, ...] = ()

    @property
    def total_width(self) -> int:
        """Width of a fully populated cell, one space between slots."""
        if not self.slots:
            return 0
        return sum(slot.width for slot in self.slots) + len(self.slots) - 1


def tag_layout(places: Iterable[Mapping[str, str]]) -> TagLayout:
    """Slot layout over the tags of *places*, the whole fleet.

    Keys are ordered by how many places carry them (widest coverage first,
    ties alphabetical), so the pairs most rows share sit leftmost and the
    cell reads as columns rather than as a sentence. Deriving this from the
    fleet and not from the filtered rows keeps the columns from jumping
    around whenever a filter hides a place.
    """
    total = 0
    counts: dict[str, int] = {}
    widths: dict[str, int] = {}
    values: dict[str, set[str]] = {}
    for tags in places:
        total += 1
        for key, value in tags.items():
            counts[key] = counts.get(key, 0) + 1
            widths[key] = max(widths.get(key, 0), len(key) + 1 + len(value))
            values.setdefault(key, set()).add(value)
    return TagLayout(
        tuple(
            TagSlot(
                key=key,
                width=widths[key],
                constant=counts[key] == total and len(values[key]) == 1,
            )
            for key in sorted(counts, key=lambda key: (-counts[key], key))
        )
    )


def fit_layout(layout: TagLayout, width: int) -> TagLayout:
    """The slots of *layout* that survive a cell cut to *width*.

    Slots go whole, rightmost first, constant keys before anything else:
    half a pair reads as a truncated value rather than as a hidden one, and
    a constant key tells the reader nothing one place at a time. The first
    slot is never dropped, so a cell always says something about the place;
    it alone can end up cropped when even one pair does not fit. Derived
    from the layout alone, not from a row: every row must drop the same
    slots or the remaining pairs stop lining up.
    """
    slots = list(layout.slots)
    while len(slots) > 1 and TagLayout(tuple(slots)).total_width > width:
        # Index 0 is out of reach in both phases: the widest-covered key
        # can itself be a constant one, and dropping it would empty the
        # cell of every place whose only pairs are constant.
        constant = [i for i, slot in enumerate(slots) if slot.constant and i > 0]
        slots.pop(constant[-1] if constant else len(slots) - 1)
    return TagLayout(tuple(slots))


def format_tags(
    tags: Mapping[str, str], layout: TagLayout, *, width: int | None = None
) -> Text | str:
    """One place's tags, laid out in *layout*'s slots.

    Each pair is padded to its slot so the same key sits at the same column
    on every row, and a place missing a key leaves that slot blank. Keys
    stay dim so the values a lab scans for stand out; a constant key is
    dimmed whole, value included. *width* is the last-resort crop for a
    layout already fitted to it, and it takes no ellipsis: the visible
    pairs stay exactly what the place carries.
    """
    if not tags:
        return "-"
    if not any(slot.key in tags for slot in layout.slots):
        # Every pair this place carries sits in a slot the cut dropped;
        # blank would read as an untagged place, which it is not.
        return "-"
    text = Text()
    for i, slot in enumerate(layout.slots):
        if i:
            text.append(" ")
        value = tags.get(slot.key)
        if value is None:
            text.append(" " * slot.width)
            continue
        if slot.constant:
            text.append(f"{slot.key}={value}", style="dim")
        else:
            text.append(f"{slot.key}=", style="dim")
            text.append(value)
        text.append(" " * (slot.width - len(slot.key) - 1 - len(value)))
    text.rstrip()
    if width is not None:
        text.truncate(width, overflow="crop")
        text.rstrip()
    return text
