"""Named coordinators: a machine-owned registry the TUI reads and writes.

Lives at ``~/.config/labgrid-tui/coordinators.toml`` (``$XDG_CONFIG_HOME``
honoured), separate from the hand-edited ``config.toml``: this file is
only ever touched through ``labgrid-tui coordinator ...`` or the in-TUI
selector, never by hand, so it is always written atomically and its
unknown keys are always round-tripped (see ``CoordinatorEntry.extra``).

    current = "lab"
    [coordinators.lab]
    address = "coordinator.example.org:20408"
    prefix = "labgrid-client -x coordinator.example.org:20408"   # optional
    [coordinators.home]
    address = "127.0.0.1:20408"
"""

import os
import re
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class CoordinatorError(Exception):
    """Any coordinator-registry problem: validation, storage, or lookup."""


class UnknownCoordinatorError(CoordinatorError):
    """A ``-x``/``coordinator`` value looked like a name but matched none."""

    def __init__(self, name: str, known: list[str]) -> None:
        self.name = name
        self.known = known
        detail = (
            "known coordinators: " + ", ".join(known)
            if known
            else "no coordinators configured (see: labgrid-tui coordinator add)"
        )
        super().__init__(f"unknown coordinator {name!r} ({detail})")


@dataclass
class CoordinatorEntry:
    name: str
    address: str
    prefix: str | None = None
    # Keys from [coordinators.NAME] this module does not model, preserved
    # verbatim across load/save so a shell built on labgrid-tui can store
    # its own per-coordinator fields (auth, ssh, ...) under the same name
    # without the coordinator editor destroying them.
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class Coordinators:
    current: str | None = None
    entries: dict[str, CoordinatorEntry] = field(default_factory=dict)


def default_coordinators_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "labgrid-tui" / "coordinators.toml"


def validate_name(name: str) -> str:
    value = name.strip()
    if not value or not _NAME_RE.fullmatch(value):
        raise CoordinatorError(
            f"invalid coordinator name {name!r}: use letters, digits, '.', '_', '-'"
        )
    return value


def validate_coordinator(address: str) -> str:
    """Validate and normalize a stored coordinator address.

    Requires an explicit ``host:port`` (split from the right so an IPv6
    host's own colons don't confuse the port), port 1-65535. Unlike
    ``-x``/``LG_COORDINATOR``/
    config.toml's ``coordinator`` key, a stored entry never gets an
    implicit default port: it is meant to be copy-pasted and reread
    without depending on what the default happens to be.
    """
    value = address.strip()
    if not value:
        raise CoordinatorError("coordinator address cannot be empty")
    if ":" not in value:
        raise CoordinatorError(f"coordinator address must be host:port, got {address!r}")
    host, _, port_str = value.rpartition(":")
    if not host:
        raise CoordinatorError(f"coordinator address must have a host, got {address!r}")
    if not port_str.isdigit():
        raise CoordinatorError(f"coordinator port must be numeric, got {address!r}")
    port = int(port_str)
    if not (1 <= port <= 65535):
        raise CoordinatorError(f"coordinator port must be between 1 and 65535, got {address!r}")
    return f"{host}:{port}"


def load_coordinators(path: Path) -> Coordinators:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return Coordinators()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CoordinatorError(f"{path}: {exc}") from exc

    raw_current = data.get("current")
    current = raw_current if isinstance(raw_current, str) else None

    entries: dict[str, CoordinatorEntry] = {}
    raw_entries = data.get("coordinators", {})
    if isinstance(raw_entries, dict):
        for name, raw in raw_entries.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                continue
            address = raw.get("address")
            if not isinstance(address, str):
                continue  # entry without a usable address: skip rather than crash
            raw_prefix = raw.get("prefix")
            prefix = raw_prefix if isinstance(raw_prefix, str) else None
            extra = {k: v for k, v in raw.items() if k not in ("address", "prefix")}
            entries[name] = CoordinatorEntry(name=name, address=address, prefix=prefix, extra=extra)
    return Coordinators(current=current, entries=entries)


def save_coordinators(path: Path, coordinators: Coordinators) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {}
    if coordinators.current is not None:
        data["current"] = coordinators.current
    if coordinators.entries:
        tables: dict[str, object] = {}
        for name, entry in coordinators.entries.items():
            table: dict[str, object] = {"address": entry.address}
            if entry.prefix is not None:
                table["prefix"] = entry.prefix
            table.update(entry.extra)
            tables[name] = table
        data["coordinators"] = tables
    payload = tomli_w.dumps(data)

    # Atomic write: a reader (this process's own next load, or a
    # concurrently running labgrid-tui) must never observe a half-written
    # file, so build the new content in a sibling temp file and rename it
    # into place: os.replace is atomic on the same filesystem.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".coordinators-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


def find_entry(value: str, coordinators: Coordinators) -> CoordinatorEntry | None:
    """Look up *value* as a coordinator name.

    Only attempted when *value* has the bare-token shape of a name (no
    ``:``; names never contain one, so a ``host:port`` address is never
    mistaken for a name). A bare word that matches no entry is not an
    error here: callers fall back to treating it as a hostname.
    """
    if not coordinators.entries or ":" in value:
        return None
    return coordinators.entries.get(value)
