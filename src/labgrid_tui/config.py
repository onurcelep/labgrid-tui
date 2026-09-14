"""Coordinator resolution and optional config file.

Resolution order: -x flag, then LG_COORDINATOR, then coordinators.toml's
``current`` entry, then the ``coordinator`` key in config.toml, then
127.0.0.1:20408 (default address and port mirror labgrid-client). The -x
flag and config.toml's ``coordinator`` key each additionally accept a
name from coordinators.toml instead of a literal address: see
``labgrid_tui.coordinators`` for how a bare token is told apart from a
hostname.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from labgrid_tui.coordinators import (
    CoordinatorError,
    Coordinators,
    find_entry,
    load_coordinators,
    validate_coordinator,
)
from labgrid_tui.model.commands import CommandTemplate

DEFAULT_PORT = 20408
DEFAULT_ADDRESS = f"127.0.0.1:{DEFAULT_PORT}"


@dataclass(frozen=True)
class Config:
    coordinator: str
    coordinator_source: str
    prefix: str | None
    capability_overrides: dict[str, str]
    proxy_set: bool
    config_error: str | None = None
    # [[commands]] entries: class-scoped templates keyed by resource class,
    # plus place-level templates (entries without a class).
    command_templates: dict[str, tuple[CommandTemplate, ...]] = field(
        default_factory=dict
    )
    place_templates: tuple[CommandTemplate, ...] = ()
    # A named coordinator's own prefix, when the active coordinator was
    # resolved via a name (see coordinators.find_entry). Only used when
    # the config.toml top-level ``prefix`` above is unset: that always
    # wins, matching the precedence documented on Config.prefix's callers.
    coordinator_prefix: str | None = None


def _normalize(address: str) -> str:
    return address if ":" in address else f"{address}:{DEFAULT_PORT}"


def resolve_coordinator(
    flag: str | None, env: Mapping[str, str], config_value: str | None = None
) -> tuple[str, str]:
    if flag:
        return _normalize(flag), "flag"
    from_env = env.get("LG_COORDINATOR")
    if from_env:
        return _normalize(from_env), "env"
    if config_value:
        return config_value, "config"
    return DEFAULT_ADDRESS, "default"


def _validate_coordinator(value: str) -> str | None:
    """Normalize a coordinator address the way ``-x`` is treated, or reject it.

    Returns ``host:port``, or ``None`` if ``value`` is not a plausible
    address. A bare host (no port) gets the default port; an IPv6 host
    must be bracketed (``[::1]`` or ``[::1]:20408``) so its own colons are
    never read as a port separator. Port checking is shared with stored
    coordinator entries so the two sources cannot drift apart.
    """
    value = value.strip()
    if not value or any(c.isspace() for c in value):
        return None
    if ":" not in value or (value.startswith("[") and value.endswith("]")):
        return f"{value}:{DEFAULT_PORT}"
    try:
        return validate_coordinator(value)
    except CoordinatorError:
        return None


def default_config_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "labgrid-tui" / "config.toml"


def load_config(
    flag: str | None,
    env: Mapping[str, str],
    config_path: Path,
    coordinators_path: Path | None = None,
) -> Config:
    prefix: str | None = None
    overrides: dict[str, str] = {}
    command_templates: dict[str, tuple[CommandTemplate, ...]] = {}
    place_templates: tuple[CommandTemplate, ...] = ()
    config_coordinator: str | None = None
    errors: list[str] = []
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        raw_coordinator = data.get("coordinator")
        if raw_coordinator is not None:
            if isinstance(raw_coordinator, str) and raw_coordinator.strip():
                config_coordinator = raw_coordinator.strip()
            else:
                errors.append(f"invalid coordinator {raw_coordinator!r}")
        raw_prefix = data.get("prefix")
        if isinstance(raw_prefix, str):
            prefix = raw_prefix
        raw_caps = data.get("capabilities", {})
        if isinstance(raw_caps, dict):
            overrides = {str(k): str(v) for k, v in raw_caps.items()}
        command_templates, place_templates, bad = _parse_commands(
            data.get("commands", [])
        )
        if bad:
            errors.append(
                f"{bad} invalid [[commands]] entr" + ("y" if bad == 1 else "ies")
                + " skipped"
            )
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as exc:
        # A missing file is normal (see above); anything else that stops us
        # reading or parsing it must not crash startup, only fall back.
        errors.append(str(exc))

    coordinators_path = (
        # Sibling of config.toml, never derived from the environment: both
        # files live in one directory, and a caller pointing at a specific
        # config.toml must not pick up another user's named coordinators.
        coordinators_path
        if coordinators_path is not None
        else config_path.parent / "coordinators.toml"
    )
    try:
        coords = load_coordinators(coordinators_path)
    except CoordinatorError as exc:
        coords = Coordinators()
        errors.append(str(exc))

    coordinator, source, coordinator_prefix = _resolve_with_names(
        flag, env, coords, config_coordinator, errors
    )

    config_error = f"{config_path}: " + "; ".join(errors) if errors else None
    return Config(
        coordinator=coordinator,
        coordinator_source=source,
        prefix=prefix,
        capability_overrides=overrides,
        proxy_set=bool(env.get("LG_PROXY")),
        config_error=config_error,
        command_templates=command_templates,
        place_templates=place_templates,
        coordinator_prefix=coordinator_prefix,
    )


def _resolve_with_names(
    flag: str | None,
    env: Mapping[str, str],
    coords: Coordinators,
    config_coordinator: str | None,
    errors: list[str],
) -> tuple[str, str, str | None]:
    """Resolve flag/env/coordinators.toml-current/config.toml-coordinator,
    each of which (except env) may name a coordinators.toml entry instead
    of a literal address. A bare word that matches no entry is a hostname
    (default port appended), exactly as before named coordinators existed,
    so adding names never changes what an unrelated ``-x host`` means.
    """
    if flag:
        entry = find_entry(flag, coords)
        if entry is not None:
            return entry.address, f"coordinator {entry.name}", entry.prefix
        validated = _validate_coordinator(flag)
        if validated is None:
            # An invalid -x value is a usage error, not a fall-through
            # condition: the caller explicitly asked for this address, so
            # silently trying LG_COORDINATOR or config.toml next would
            # connect somewhere the user didn't ask for.
            raise CoordinatorError(f"invalid coordinator {flag!r}")
        return validated, "flag", None

    from_env = env.get("LG_COORDINATOR")
    if from_env:
        validated = _validate_coordinator(from_env)
        if validated is None:
            # Unlike -x, an environment variable can be stale or wrong
            # without the user noticing; report it the way a bad
            # config.toml value is reported and keep resolving.
            errors.append(f"invalid coordinator {from_env!r} (from LG_COORDINATOR)")
        else:
            return validated, "env", None

    current_entry = coords.entries.get(coords.current) if coords.current else None
    if current_entry is not None:
        return current_entry.address, f"coordinator {current_entry.name}", current_entry.prefix

    if config_coordinator:
        entry = find_entry(config_coordinator, coords)
        if entry is not None:
            return entry.address, f"coordinator {entry.name}", entry.prefix
        validated = _validate_coordinator(config_coordinator)
        if validated is None:
            errors.append(f"invalid coordinator {config_coordinator!r}")
            return DEFAULT_ADDRESS, "default", None
        return validated, "config", None

    return DEFAULT_ADDRESS, "default", None


def _parse_commands(
    raw: object,
) -> tuple[dict[str, tuple[CommandTemplate, ...]], tuple[CommandTemplate, ...], int]:
    """Parse [[commands]] tables into templates; count invalid entries."""
    by_class: dict[str, tuple[CommandTemplate, ...]] = {}
    place: list[CommandTemplate] = []
    bad = 0
    if not isinstance(raw, list):
        return by_class, (), 1 if raw else 0
    for item in raw:
        if not isinstance(item, dict):
            bad += 1
            continue
        category = item.get("category")
        label = item.get("label")
        suffix = item.get("suffix")
        if not (isinstance(category, str) and isinstance(label, str)
                and isinstance(suffix, str)):
            bad += 1
            continue
        template = CommandTemplate(
            category,
            label,
            suffix,
            interactive=bool(item.get("interactive", False)),
            needs_args=bool(item.get("needs_args", "<" in suffix)),
            name_style=str(item.get("name_style", "none")),
        )
        cls = item.get("class")
        if isinstance(cls, str) and cls:
            by_class[cls] = by_class.get(cls, ()) + (template,)
        else:
            place.append(template)
    return by_class, tuple(place), bad
