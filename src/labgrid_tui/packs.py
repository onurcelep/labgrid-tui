"""Command packs: user-registered TOML files of copy-only command recipes,
shared as team command sets and shown as their own tab per pack.

Two files are involved. ``packs.toml`` (``~/.config/labgrid-tui/packs.toml``,
``$XDG_CONFIG_HOME`` honoured) is the registry: a machine-owned file this
module reads and writes atomically, with unknown per-entry keys round-tripped
the same way ``coordinators.py`` preserves ``CoordinatorEntry.extra``:
never hand-edited, only touched through ``labgrid-tui pack ...``::

    [[packs]]
    name = "robot"
    source = "/home/me/qa/robot.toml"          # path: read in place every start
    [[packs]]
    name = "team"
    source = "https://git.example.org/raw/team.toml"
    cached = "~/.config/labgrid-tui/packs/team.toml"   # URL: fetched into cache

The pack file itself (``robot.toml`` above) is never auto-discovered:
only ``pack add PATH|URL`` registers one. It is documented in
``README.md (Command packs)``. A path pack is read from *source* fresh on every start
(so e.g. a git pull to that file takes effect immediately); a URL pack is
served from *cached* and only re-fetched by ``pack update``.

Registry entries preserve the order they were added in (see
``load_registry``/``save_registry``): that order becomes tab order in the
command overlay, so ``pack list``/``pack add``/``pack remove`` never
reorders a team's existing tabs as a side effect.
"""

import os
import re
import tempfile
import tomllib
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlparse

import tomli_w

from labgrid_tui.model.packs import Pack, PackCommandTemplate, parse_template

_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_RESERVED_NAMES = {".", ".."}
_REQUIRES_RE = re.compile(r"^(res\.[A-Za-z0-9_]+|tag\.\S+|held)$")
# Place verbs the dashboard dispatches by label (see
# DashboardScreen._dispatch_verb): a pack entry with one of these labels
# would otherwise be indistinguishable from the built-in command it shadows.
_RESERVED_LABELS = {"acquire", "release"}
FETCH_TIMEOUT = 10.0
# Cap on a fetched pack file's size: a pack is a short recipe list, not a
# place to smuggle an unbounded download through an otherwise-trusted path.
MAX_PACK_BYTES = 1_000_000


class PackError(Exception):
    """Any pack problem: validation, storage, fetch, or parse."""


@dataclass
class PackRegistryEntry:
    name: str
    source: str
    cached: str | None = None
    # Keys from [[packs]] this module does not model, preserved verbatim
    # across load/save (see coordinators.CoordinatorEntry.extra).
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class PackRegistry:
    packs: dict[str, PackRegistryEntry] = field(default_factory=dict)


def default_packs_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "labgrid-tui" / "packs.toml"


def default_pack_cache_dir(env: Mapping[str, str]) -> Path:
    return default_packs_path(env).parent / "packs"


def validate_pack_name(name: str) -> str:
    value = name.strip()
    if not value or value in _RESERVED_NAMES or not _NAME_RE.fullmatch(value):
        raise PackError(f"invalid pack name {name!r}: use letters, digits, '.', '_', '-'")
    return value


def is_url(source: str) -> bool:
    return urlparse(source).scheme == "https"


def resolve_source_path(entry: PackRegistryEntry) -> Path:
    """Where to read *entry* from: its cache for a URL pack, its source
    path directly otherwise."""
    if entry.cached is not None:
        return Path(entry.cached).expanduser()
    return Path(entry.source).expanduser()


# ----------------------------------------------------------------------
# Registry: load/save (same atomic-write convention as coordinators.py)
# ----------------------------------------------------------------------


def load_registry(path: Path) -> PackRegistry:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        return PackRegistry()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"{path}: {exc}") from exc

    packs: dict[str, PackRegistryEntry] = {}
    raw_packs = data.get("packs", [])
    if isinstance(raw_packs, list):
        for raw in raw_packs:
            if not isinstance(raw, dict):
                continue
            name = raw.get("name")
            source = raw.get("source")
            if not isinstance(name, str) or not isinstance(source, str):
                continue  # entry without name/source: skip rather than crash
            raw_cached = raw.get("cached")
            cached = raw_cached if isinstance(raw_cached, str) else None
            extra = {k: v for k, v in raw.items() if k not in ("name", "source", "cached")}
            packs[name] = PackRegistryEntry(name=name, source=source, cached=cached, extra=extra)
    return PackRegistry(packs=packs)


def save_registry(path: Path, registry: PackRegistry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tables: list[dict[str, object]] = []
    # Dict (insertion) order, not sorted: this is the order shown in tabs
    # and `pack list`, so saving must never reorder existing entries.
    for entry in registry.packs.values():
        table: dict[str, object] = {"name": entry.name, "source": entry.source}
        if entry.cached is not None:
            table["cached"] = entry.cached
        table.update(entry.extra)
        tables.append(table)
    payload = tomli_w.dumps({"packs": tables} if tables else {})

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".packs-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise


# ----------------------------------------------------------------------
# Pack files: parse (never raises: malformed entries are skipped and
# counted, matching config._parse_commands's tolerance)
# ----------------------------------------------------------------------


def _parse_command(item: object) -> tuple[PackCommandTemplate | None, str | None]:
    if not isinstance(item, dict):
        return None, "invalid [[commands]] entry: not a table"
    label = item.get("label")
    command = item.get("command")
    if not (isinstance(label, str) and label.strip()):
        return None, "invalid [[commands]] entry: label is required"
    if label.strip().lower() in _RESERVED_LABELS:
        return None, (
            f"invalid [[commands]] entry {label!r}: label collides with the "
            "built-in Acquire/Release verbs"
        )
    if not (isinstance(command, str) and command.strip()):
        return None, f"invalid [[commands]] entry {label!r}: command is required"
    _, template_error = parse_template(command)
    if template_error is not None:
        return None, f"invalid [[commands]] entry {label!r}: {template_error}"
    raw_requires = item.get("requires", [])
    if not isinstance(raw_requires, list) or not all(isinstance(r, str) for r in raw_requires):
        return None, f"invalid [[commands]] entry {label!r}: requires must be a list of strings"
    bad = [r for r in raw_requires if not _REQUIRES_RE.fullmatch(r)]
    if bad:
        return None, f"invalid [[commands]] entry {label!r}: bad requires {bad!r}"
    return PackCommandTemplate(label=label, command=command, requires=frozenset(raw_requires)), None


def parse_pack_toml(data: object, source: str) -> tuple[Pack | None, list[str]]:
    """Parse an already-``tomllib``-loaded pack file. A missing/invalid
    ``[pack]`` table fails the whole file (nothing to name a tab with);
    individual bad ``[[commands]]`` entries are skipped and reported."""
    if not isinstance(data, dict):
        return None, [f"{source}: not a table"]
    raw_pack = data.get("pack")
    if not isinstance(raw_pack, dict):
        return None, [f"{source}: missing [pack] table"]
    raw_name = raw_pack.get("name")
    if not isinstance(raw_name, str):
        return None, [f"{source}: [pack].name is required"]
    try:
        name = validate_pack_name(raw_name)
    except PackError as exc:
        return None, [f"{source}: {exc}"]
    raw_description = raw_pack.get("description", "")
    description = raw_description if isinstance(raw_description, str) else ""

    errors: list[str] = []
    commands: list[PackCommandTemplate] = []
    raw_commands = data.get("commands", [])
    if not isinstance(raw_commands, list):
        errors.append(f"{source}: [[commands]] must be an array of tables")
        raw_commands = []
    for item in raw_commands:
        parsed, err = _parse_command(item)
        if err is not None:
            errors.append(f"{source}: {err}")
            continue
        if parsed is not None:
            commands.append(parsed)
    return Pack(name=name, description=description, source=source, commands=tuple(commands)), errors


def parse_pack_bytes(raw: bytes, source: str) -> tuple[Pack | None, list[str]]:
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        return None, [f"{source}: {exc}"]
    return parse_pack_toml(data, source)


def read_pack_file(path: Path, source: str) -> tuple[Pack | None, list[str]]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, [f"{path}: not found"]
    except OSError as exc:
        return None, [f"{path}: {exc}"]
    return parse_pack_bytes(raw, source)


def fetch_url(url: str, *, timeout: float = FETCH_TIMEOUT) -> bytes:
    """Fetch *url*: https only, 10s timeout, capped at MAX_PACK_BYTES
    (see README.md (Command packs)).

    The https check is repeated against the *final* URL after redirects:
    urlopen follows a 30x wherever it points, so an https source can still
    hand back a response from a plain-http destination unless that
    destination is checked too.
    """
    if not is_url(url):
        raise PackError(f"unsupported pack source {url!r}: use an https:// URL or a file path")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            final_url = response.geturl()
            if not is_url(final_url):
                raise PackError(f"{url}: redirected to non-https URL {final_url!r}")
            raw = response.read(MAX_PACK_BYTES + 1)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise PackError(f"{url}: {exc}") from exc
    if len(raw) > MAX_PACK_BYTES:
        raise PackError(f"{url}: response exceeds {MAX_PACK_BYTES} bytes")
    return bytes(raw)


# ----------------------------------------------------------------------
# Loading every registered pack (shared by the CLI and app startup)
# ----------------------------------------------------------------------


@dataclass
class LoadedPack:
    entry: PackRegistryEntry
    pack: Pack | None
    errors: list[str]


def load_registered_packs(registry: PackRegistry) -> list[LoadedPack]:
    """Read every registered pack file, in registration order. A pack
    whose file is missing or invalid comes back with ``pack=None`` and
    its errors: callers skip it without crashing (see README.md (Command packs)).

    The registry name (the key ``pack add``/``--name`` registered it
    under) always wins over the file's own ``[pack].name`` for the
    loaded ``Pack.name``: that field becomes the command overlay tab
    and palette group, which must match what ``pack show``/``pack
    remove`` address, not whatever a pack file happens to declare.
    """
    results: list[LoadedPack] = []
    for entry in registry.packs.values():
        path = resolve_source_path(entry)
        pack, errors = read_pack_file(path, entry.source)
        if pack is not None and pack.name != entry.name:
            pack = replace(pack, name=entry.name)
        results.append(LoadedPack(entry=entry, pack=pack, errors=errors))
    return results


def fetch_pack(source: str) -> tuple[Pack | None, bytes | None, list[str]]:
    """Load *source* (fetching it if it's a URL) without touching the
    registry. Returns the parsed pack (or ``None`` on failure), the raw
    bytes for a URL source (``None`` for a path, which is read fresh on
    every start instead of cached), and any entry-level errors."""
    if is_url(source):
        raw = fetch_url(source)
        pack, errors = parse_pack_bytes(raw, source)
        return pack, raw, errors
    path = Path(source).expanduser()
    pack, errors = read_pack_file(path, str(path))
    return pack, None, errors


def cache_pack(cache_dir: Path, name: str, raw: bytes) -> str:
    """Write a URL pack's fetched bytes to its cache file; returns the
    path stored as the registry entry's ``cached``."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{name}.toml"
    cache_path.write_bytes(raw)
    return str(cache_path)
