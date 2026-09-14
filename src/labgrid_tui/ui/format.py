"""Presentation helpers: capability chips, humanized ages, dynamic tag columns.

The rendering vocabulary (colored capability abbreviations, status dots,
"5d ago" timestamps) stays generic labgrid: nothing lab-specific.
"""

from collections.abc import Iterable

from rich.text import Text

from labgrid_tui.coordinator.models import Place

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


def top_tag_keys(places: Iterable[Place], limit: int = 3) -> list[str]:
    """The most common tag keys across the fleet, for dynamic table columns."""
    counts: dict[str, int] = {}
    for place in places:
        for key in place.tags:
            counts[key] = counts.get(key, 0) + 1
    return sorted(counts, key=lambda k: (-counts[k], k))[:limit]
