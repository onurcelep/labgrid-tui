"""Presentation helpers: capability chips, humanized ages, tag pairs.

The rendering vocabulary (colored capability abbreviations, status dots,
"5d ago" timestamps) stays generic labgrid: nothing lab-specific.
"""

from collections.abc import Mapping

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


def format_tags(tags: Mapping[str, str], width: int | None = None) -> Text | str:
    """Tags on one line, the way labgrid-client prints them.

    Sorted ``key=value`` pairs separated by a single space, keys dimmed so
    the values a lab actually scans for stand out. Cut to *width* without
    an ellipsis: the visible pairs stay exactly what the place carries.
    """
    if not tags:
        return "-"
    text = Text()
    for i, (key, value) in enumerate(sorted(tags.items())):
        if i:
            text.append(" ")
        text.append(f"{key}=", style="dim")
        text.append(value)
    if width is not None:
        text.truncate(width, overflow="crop")
    return text
