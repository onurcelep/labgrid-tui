"""CLI entry point: run the TUI, or inspect/create its config file."""

import argparse
import os
import sys
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from labgrid_tui.config import default_config_path, load_config
from labgrid_tui.coordinators import (
    CoordinatorEntry,
    CoordinatorError,
    Coordinators,
    default_coordinators_path,
    load_coordinators,
    save_coordinators,
    validate_coordinator,
    validate_name,
)
from labgrid_tui.model.commands import default_prefix
from labgrid_tui.packs import (
    PackError,
    PackRegistry,
    PackRegistryEntry,
    cache_pack,
    default_pack_cache_dir,
    default_packs_path,
    fetch_pack,
    is_url,
    load_registered_packs,
    load_registry,
    read_pack_file,
    resolve_source_path,
    save_registry,
    validate_pack_name,
)
from labgrid_tui.ui.app import LabgridTuiApp

PROXY_MESSAGE = (
    "LG_PROXY is set, but SSH-proxied coordinator connections are not "
    "supported by labgrid-tui. Unset LG_PROXY or use a directly reachable "
    "coordinator address."
)

CONFIG_TEMPLATE = """\
# labgrid-tui config file. Every key is optional, and so is this file:
# a missing config.toml is the normal case. Run "labgrid-tui config show"
# after editing to see what got picked up.

# Coordinator address (host[:port], or a name from
# ~/.config/labgrid-tui/coordinators.toml, see "labgrid-tui coordinator").
# Overridden by -x/--coordinator, then by LG_COORDINATOR, then by
# coordinators.toml's active coordinator. Falls back to 127.0.0.1:20408
# if none of these are set.
# coordinator = "127.0.0.1:20408"

# Command-line prefix used to build every command line the TUI shows.
# Defaults to "labgrid-client -x <coordinator>".
# prefix = "labgrid-client -x other.lab:20408"

# Extra resource-class to capability-tag mappings, merged with the
# built-in table (config wins on conflicts).
# [capabilities]
# MyCustomResource = "power"

# Custom command entries. Omit "class" for a place-level entry (shown for
# every place); set it to a resource class name to scope the entry to
# places exporting that class.
# [[commands]]
# category = "Custom"
# label = "Ping DUT"
# suffix = "ssh -- ping -c1 10.0.0.1"
# class = "NetworkService"
"""


def _common_parser() -> argparse.ArgumentParser:
    """Flags accepted at every subcommand level, not just before it.

    ``labgrid-tui --config X config show``, ``labgrid-tui config --config X
    show``, and ``labgrid-tui config show --config X`` must all resolve the
    same ``args.config``/``args.coordinator``: argparse only recognizes an
    option at the parser level a token belongs to, so each subparser needs
    its own copy of these via ``parents=`` rather than relying on the
    top-level parser alone.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-x", "--coordinator", metavar="HOST[:PORT]",
        # SUPPRESS, not the implicit None: argparse's subparser dispatch
        # parses each level into its own fresh namespace and then copies
        # every attribute onto the parent one, so a subparser-level default
        # of None would silently clobber a value already given at the
        # parent level (e.g. "--coordinator X config show") whenever the
        # flag isn't repeated at the subparser level too. SUPPRESS omits
        # the attribute entirely when unset, so the copy only ever carries
        # a value the user actually typed at that level.
        default=argparse.SUPPRESS,
        help="coordinator address (default: LG_COORDINATOR, then config file, "
        "then 127.0.0.1:20408)",
    )
    common.add_argument(
        "--config", metavar="PATH", type=Path, default=argparse.SUPPRESS,
        help="path to config.toml (default: $XDG_CONFIG_HOME/labgrid-tui/config.toml)",
    )
    return common


def _build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        prog="labgrid-tui",
        description="Terminal dashboard for labgrid labs.",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="subcommand")
    config_parser = subparsers.add_parser(
        "config", help="inspect or create the config file", parents=[common]
    )
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("path", help="print the config file path", parents=[common])
    config_sub.add_parser("show", help="print the resolved configuration",
                           parents=[common])
    init_parser = config_sub.add_parser(
        "init", help="create a template config file", parents=[common]
    )
    init_parser.add_argument(
        "--force", action="store_true", help="overwrite an existing config file"
    )

    coordinator_parser = subparsers.add_parser(
        "coordinator", help="manage named coordinators (coordinators.toml)",
        parents=[common],
    )
    coordinator_sub = coordinator_parser.add_subparsers(
        dest="coordinator_command", required=True
    )
    coordinator_sub.add_parser("list", help="list configured coordinators",
                                parents=[common])

    add_parser = coordinator_sub.add_parser(
        "add", help="add a coordinator", parents=[common]
    )
    add_parser.add_argument("name")
    add_parser.add_argument("address", metavar="ADDRESS", help="host:port")
    add_parser.add_argument("--prefix", help="command-line prefix for this coordinator")
    add_parser.add_argument("--use", action="store_true", help="make it the active coordinator")

    use_parser = coordinator_sub.add_parser(
        "use", help="switch the active coordinator", parents=[common]
    )
    use_parser.add_argument("name")

    remove_parser = coordinator_sub.add_parser(
        "remove", help="remove a coordinator", parents=[common]
    )
    remove_parser.add_argument("name")
    remove_parser.add_argument(
        "--force", action="store_true",
        help="remove it even if it is the active coordinator (clears the active coordinator)",
    )

    show_coord_parser = coordinator_sub.add_parser(
        "show", help="show one coordinator, or the active one", parents=[common]
    )
    show_coord_parser.add_argument("name", nargs="?")

    edit_parser = coordinator_sub.add_parser(
        "edit", help="edit a coordinator", parents=[common]
    )
    edit_parser.add_argument("name")
    edit_parser.add_argument("--address", help="new host:port")
    prefix_group = edit_parser.add_mutually_exclusive_group()
    prefix_group.add_argument("--prefix", help="new command-line prefix")
    prefix_group.add_argument(
        "--no-prefix", action="store_true", help="clear the per-coordinator prefix override"
    )

    pack_parser = subparsers.add_parser(
        "pack", help="manage command packs (packs.toml)", parents=[common]
    )
    pack_sub = pack_parser.add_subparsers(dest="pack_command", required=True)
    pack_sub.add_parser("list", help="list registered command packs", parents=[common])

    pack_add_parser = pack_sub.add_parser(
        "add", help="add a command pack", parents=[common]
    )
    pack_add_parser.add_argument("source", metavar="PATH|URL")
    pack_add_parser.add_argument(
        "--name", help="registry name (default: the pack file's own [pack].name)"
    )

    pack_remove_parser = pack_sub.add_parser(
        "remove", help="remove a command pack", parents=[common]
    )
    pack_remove_parser.add_argument("name")

    pack_update_parser = pack_sub.add_parser(
        "update", help="re-fetch URL-sourced command packs", parents=[common]
    )
    pack_update_parser.add_argument(
        "name", nargs="?", help="only this pack (default: every URL-sourced pack)"
    )

    pack_show_parser = pack_sub.add_parser(
        "show", help="show one command pack's entries", parents=[common]
    )
    pack_show_parser.add_argument("name")

    return parser


def _cmd_config_path(config_path: Path) -> int:
    print(config_path)
    return 0


def _cmd_config_show(
    coordinator_flag: str | None, env: Mapping[str, str], config_path: Path
) -> int:
    try:
        config = load_config(coordinator_flag, env, config_path)
    except CoordinatorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not config_path.exists():
        config_status = "missing"
    elif config.config_error:
        detail = config.config_error.removeprefix(f"{config_path}: ")
        config_status = f"error: {detail}"
    else:
        config_status = "ok"
    print(f"config: {config_path} ({config_status})")
    print(f"coordinator: {config.coordinator} (from {config.coordinator_source})")
    if config.prefix is not None:
        print(f"prefix: {config.prefix} (from config)")
    elif config.coordinator_prefix is not None:
        print(f"prefix: {config.coordinator_prefix} (from coordinator)")
    else:
        print(f"prefix: {default_prefix(config.coordinator)} (default)")
    print(f"capabilities: {len(config.capability_overrides)} custom")
    command_count = (
        sum(len(templates) for templates in config.command_templates.values())
        + len(config.place_templates)
    )
    print(f"commands: {command_count} custom")
    if config.proxy_set:
        print("proxy: LG_PROXY set (unsupported)")

    coordinators_path = default_coordinators_path(env)
    try:
        coordinators = load_coordinators(coordinators_path)
        coordinators_status = "ok" if coordinators_path.exists() else "missing"
    except CoordinatorError as exc:
        coordinators = Coordinators()
        coordinators_status = f"error: {exc}"
    print(f"coordinators: {coordinators_path} ({coordinators_status})")
    if coordinators.current and coordinators.current in coordinators.entries:
        entry = coordinators.entries[coordinators.current]
        print(f"active coordinator: {entry.name} ({entry.address})")
    elif coordinators.current:
        print(f"active coordinator: {coordinators.current} (unknown, dangling reference)")
    else:
        print("active coordinator: none")

    packs_path = default_packs_path(env)
    pack_error = False
    registry: PackRegistry | None
    try:
        registry = load_registry(packs_path)
        packs_status = "ok" if packs_path.exists() else "missing"
    except PackError as exc:
        registry = None
        packs_status = f"error: {exc}"
        pack_error = True
    print(f"packs: {packs_path} ({packs_status})")
    if registry is not None:
        for loaded in load_registered_packs(registry):
            count = len(loaded.pack.commands) if loaded.pack is not None else 0
            status = "ok" if not loaded.errors else "error"
            if loaded.errors:
                pack_error = True
            print(f"  {loaded.entry.name}: {count} commands ({status})")

    return 1 if (config.config_error or config.proxy_set or pack_error) else 0


def _cmd_config_init(config_path: Path, *, force: bool) -> int:
    if config_path.exists() and not force:
        print(f"{config_path} (already exists)")
        return 1
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(CONFIG_TEMPLATE)
    print(f"{config_path} (created)")
    return 0


def _cmd_coordinator_list(coordinators_path: Path) -> int:
    coordinators = load_coordinators(coordinators_path)
    if not coordinators.entries:
        print("no coordinators configured")
        return 0
    for name in sorted(coordinators.entries):
        entry = coordinators.entries[name]
        marker = "*" if name == coordinators.current else " "
        print(f"{marker} {name}  {entry.address}")
    return 0


def _cmd_coordinator_add(
    coordinators_path: Path, name: str, address: str, prefix: str | None, use: bool
) -> int:
    try:
        name = validate_name(name)
        address = validate_coordinator(address)
    except CoordinatorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    coordinators = load_coordinators(coordinators_path)
    if name in coordinators.entries:
        print(f"coordinator {name!r} already exists", file=sys.stderr)
        return 2
    coordinators.entries[name] = CoordinatorEntry(name=name, address=address, prefix=prefix)
    if use:
        coordinators.current = name
    save_coordinators(coordinators_path, coordinators)
    print(f"added coordinator {name} ({address})")
    return 0


def _cmd_coordinator_use(coordinators_path: Path, name: str) -> int:
    coordinators = load_coordinators(coordinators_path)
    if name not in coordinators.entries:
        known = ", ".join(sorted(coordinators.entries)) or "(none)"
        print(f"unknown coordinator {name!r}; known coordinators: {known}", file=sys.stderr)
        return 2
    coordinators.current = name
    save_coordinators(coordinators_path, coordinators)
    print(f"using coordinator {name}")
    return 0


def _cmd_coordinator_remove(coordinators_path: Path, name: str, *, force: bool) -> int:
    coordinators = load_coordinators(coordinators_path)
    if name not in coordinators.entries:
        print(f"unknown coordinator {name!r}", file=sys.stderr)
        return 2
    if name == coordinators.current and not force:
        print(
            f"{name!r} is the active coordinator; pass --force to remove it "
            "(this clears the active coordinator)",
            file=sys.stderr,
        )
        return 2
    del coordinators.entries[name]
    if coordinators.current == name:
        coordinators.current = None
    save_coordinators(coordinators_path, coordinators)
    print(f"removed coordinator {name}")
    return 0


def _cmd_coordinator_show(coordinators_path: Path, name: str | None) -> int:
    coordinators = load_coordinators(coordinators_path)
    target = name or coordinators.current
    if target is None:
        print("no active coordinator")
        return 1
    entry = coordinators.entries.get(target)
    if entry is None:
        print(f"unknown coordinator {target!r}", file=sys.stderr)
        return 2
    marker = " (active)" if target == coordinators.current else ""
    print(f"name: {entry.name}{marker}")
    print(f"address: {entry.address}")
    if entry.prefix is not None:
        print(f"prefix: {entry.prefix}")
    else:
        print(f"prefix: {default_prefix(entry.address)} (default)")
    return 0


def _cmd_coordinator_edit(
    coordinators_path: Path,
    name: str,
    address: str | None,
    prefix: str | None,
    *,
    no_prefix: bool,
) -> int:
    coordinators = load_coordinators(coordinators_path)
    entry = coordinators.entries.get(name)
    if entry is None:
        print(f"unknown coordinator {name!r}", file=sys.stderr)
        return 2
    new_address = entry.address
    if address is not None:
        try:
            new_address = validate_coordinator(address)
        except CoordinatorError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    new_prefix = entry.prefix
    if no_prefix:
        new_prefix = None
    elif prefix is not None:
        new_prefix = prefix
    coordinators.entries[name] = replace(entry, address=new_address, prefix=new_prefix)
    save_coordinators(coordinators_path, coordinators)
    print(f"updated coordinator {name}")
    return 0


def _cmd_pack_list(packs_path: Path) -> int:
    registry = load_registry(packs_path)
    if not registry.packs:
        print("no command packs registered")
        return 0
    had_error = False
    for loaded in load_registered_packs(registry):
        entry = loaded.entry
        if loaded.pack is None:
            had_error = True
            print(f"{entry.name}  {entry.source}  error: {'; '.join(loaded.errors)}")
            continue
        note = f"  ({len(loaded.errors)} entries skipped)" if loaded.errors else ""
        had_error = had_error or bool(loaded.errors)
        print(f"{entry.name}  {entry.source}  {len(loaded.pack.commands)} commands{note}")
    return 1 if had_error else 0


def _cmd_pack_add(
    packs_path: Path, env: Mapping[str, str], source: str, name_override: str | None
) -> int:
    try:
        pack, raw, errors = fetch_pack(source)
    except PackError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if pack is None:
        print(f"{source}: " + "; ".join(errors), file=sys.stderr)
        return 1

    try:
        name = validate_pack_name(name_override) if name_override else validate_pack_name(
            pack.name
        )
    except PackError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    registry = load_registry(packs_path)
    if name in registry.packs:
        print(f"pack {name!r} already exists", file=sys.stderr)
        return 2

    cached = cache_pack(default_pack_cache_dir(env), name, raw) if raw is not None else None
    registry.packs[name] = PackRegistryEntry(name=name, source=source, cached=cached)
    save_registry(packs_path, registry)
    note = f" ({len(errors)} entries skipped)" if errors else ""
    print(f"added pack {name} ({len(pack.commands)} commands){note}")
    return 0


def _cmd_pack_remove(packs_path: Path, name: str) -> int:
    registry = load_registry(packs_path)
    entry = registry.packs.pop(name, None)
    if entry is None:
        print(f"unknown pack {name!r}", file=sys.stderr)
        return 2
    if entry.cached is not None:
        try:
            Path(entry.cached).expanduser().unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"warning: could not remove cache {entry.cached}: {exc}", file=sys.stderr)
    save_registry(packs_path, registry)
    print(f"removed pack {name}")
    return 0


def _cmd_pack_update(packs_path: Path, env: Mapping[str, str], name: str | None) -> int:
    registry = load_registry(packs_path)
    if name is not None and name not in registry.packs:
        print(f"unknown pack {name!r}", file=sys.stderr)
        return 2
    targets = [registry.packs[name]] if name is not None else list(registry.packs.values())
    url_targets = [entry for entry in targets if is_url(entry.source)]
    had_error = False
    for entry in url_targets:
        try:
            pack, raw, errors = fetch_pack(entry.source)
        except PackError as exc:
            print(f"{entry.name}: {exc}", file=sys.stderr)
            had_error = True
            continue
        if pack is None or raw is None:
            print(f"{entry.name}: " + "; ".join(errors), file=sys.stderr)
            had_error = True
            continue
        cached = cache_pack(default_pack_cache_dir(env), entry.name, raw)
        registry.packs[entry.name] = PackRegistryEntry(
            name=entry.name, source=entry.source, cached=cached, extra=entry.extra
        )
        note = f" ({len(errors)} entries skipped)" if errors else ""
        print(f"updated pack {entry.name} ({len(pack.commands)} commands){note}")
    if url_targets:
        save_registry(packs_path, registry)
    else:
        print("no URL-sourced packs to update")
    return 1 if had_error else 0


def _cmd_pack_show(packs_path: Path, name: str) -> int:
    registry = load_registry(packs_path)
    entry: PackRegistryEntry | None = registry.packs.get(name)
    if entry is None:
        print(f"unknown pack {name!r}", file=sys.stderr)
        return 2
    pack, errors = read_pack_file(resolve_source_path(entry), entry.source)
    if pack is None:
        print(f"{name}: " + "; ".join(errors), file=sys.stderr)
        return 1
    print(f"name: {name}")
    print(f"source: {entry.source}")
    if pack.description:
        print(f"description: {pack.description}")
    for cmd in pack.commands:
        suffix = f"  requires: {', '.join(sorted(cmd.requires))}" if cmd.requires else ""
        print(f"- {cmd.label}: {cmd.command}{suffix}")
    if errors:
        for error in errors:
            print(f"warning: {error}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    env = os.environ
    # --config/-x are declared (via the shared parent parser) at multiple
    # levels so they can appear before or after a subcommand; whichever
    # level the user gave them at is the only one with the attribute set
    # (see _common_parser's SUPPRESS default), so look them up with
    # getattr instead of assuming args.config/args.coordinator exist.
    config_flag: Path | None = getattr(args, "config", None)
    coordinator_flag: str | None = getattr(args, "coordinator", None)
    config_path: Path = config_flag if config_flag is not None else default_config_path(env)

    if args.subcommand == "config":
        if args.config_command == "path":
            return _cmd_config_path(config_path)
        if args.config_command == "show":
            return _cmd_config_show(coordinator_flag, env, config_path)
        return _cmd_config_init(config_path, force=args.force)

    if args.subcommand == "coordinator":
        coordinators_path = default_coordinators_path(env)
        if args.coordinator_command == "list":
            return _cmd_coordinator_list(coordinators_path)
        if args.coordinator_command == "add":
            return _cmd_coordinator_add(
                coordinators_path, args.name, args.address, args.prefix, args.use
            )
        if args.coordinator_command == "use":
            return _cmd_coordinator_use(coordinators_path, args.name)
        if args.coordinator_command == "remove":
            return _cmd_coordinator_remove(coordinators_path, args.name, force=args.force)
        if args.coordinator_command == "show":
            return _cmd_coordinator_show(coordinators_path, args.name)
        return _cmd_coordinator_edit(
            coordinators_path, args.name, args.address, args.prefix, no_prefix=args.no_prefix
        )

    if args.subcommand == "pack":
        packs_path = default_packs_path(env)
        if args.pack_command == "list":
            return _cmd_pack_list(packs_path)
        if args.pack_command == "add":
            return _cmd_pack_add(packs_path, env, args.source, args.name)
        if args.pack_command == "remove":
            return _cmd_pack_remove(packs_path, args.name)
        if args.pack_command == "update":
            return _cmd_pack_update(packs_path, env, args.name)
        return _cmd_pack_show(packs_path, args.name)

    try:
        config = load_config(coordinator_flag, env, config_path)
    except CoordinatorError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if config.proxy_set:
        print(PROXY_MESSAGE, file=sys.stderr)
        return 1
    LabgridTuiApp(config).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
