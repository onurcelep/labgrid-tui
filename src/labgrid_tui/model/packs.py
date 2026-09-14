"""Command pack templates and the placeholder/gating engine.

A pack entry is always copy-only (``CommandTemplate.copy_only``, enforced by
``CliActionRunner.run``): it renders a team-authored command line against
one place's resources, tags, and reservation, and the TUI never executes
it. Gating mirrors ``model.commands.evaluate()``'s shape: UNAVAILABLE
with a short reason instead of raising, but resolution is placeholder
substitution against ``PlaceholderContext``, not the resource-class
template tables in ``model/commands.py``.
"""

import string
from dataclasses import dataclass, field

from labgrid_tui.coordinator.models import Place, Reservation, Resource
from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState, find_reservation

_FORMATTER = string.Formatter()


@dataclass(frozen=True)
class PackCommandTemplate:
    label: str
    command: str
    requires: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Pack:
    name: str
    description: str
    source: str
    commands: tuple[PackCommandTemplate, ...]


@dataclass(frozen=True)
class PlaceholderContext:
    place: str
    coordinator: str
    prefix: str
    token: str | None
    tags: dict[str, str]
    resources_by_class: dict[str, Resource]


def _held_by_me(place: Place, me: str, reservations: list[Reservation] | None) -> bool:
    """ "held" per README.md (Command packs): acquired by me (or allowed), or the
    place's reservation belongs to me: independent of that
    reservation's state (waiting/allocated), unlike the acquire gating in
    model/commands.py."""
    if place.acquired == me or me in place.allowed:
        return True
    if place.reservation:
        reservation = find_reservation(place.reservation, reservations)
        if reservation is not None and reservation.owner == me:
            return True
    return False


def _my_token(place: Place, me: str, reservations: list[Reservation] | None) -> str | None:
    if not place.reservation:
        return None
    reservation = find_reservation(place.reservation, reservations)
    if reservation is not None and reservation.owner == me:
        return reservation.token
    return None


def build_context(
    place: Place,
    resources: list[Resource],
    me: str,
    reservations: list[Reservation] | None,
    coordinator: str,
    prefix: str,
) -> PlaceholderContext:
    resources_by_class: dict[str, Resource] = {}
    for resource in resources:
        # First matched resource of a class wins, per README.md (Command packs):
        # ambiguity among several is left to the pack author's `requires`.
        resources_by_class.setdefault(resource.cls, resource)
    return PlaceholderContext(
        place=place.name,
        coordinator=coordinator,
        prefix=prefix,
        token=_my_token(place, me, reservations),
        tags=place.tags,
        resources_by_class=resources_by_class,
    )


def _resolve_field(field_name: str, ctx: PlaceholderContext) -> tuple[str | None, str | None]:
    """Resolve one ``{field_name}``. Returns ``(value, reason)`` where
    exactly one side is not ``None``; never raises, per README.md (Command packs)'s
    "unknown keys never raise"."""
    if field_name == "place":
        return ctx.place, None
    if field_name == "coordinator":
        return ctx.coordinator, None
    if field_name == "prefix":
        return ctx.prefix, None
    if field_name == "token":
        if ctx.token is None:
            return None, "needs reservation token"
        return ctx.token, None
    if field_name.startswith("tag."):
        key = field_name.removeprefix("tag.")
        value = ctx.tags.get(key) if key else None
        if value is None:
            return None, f"needs tag {key}" if key else f"invalid placeholder {{{field_name}}}"
        return value, None
    if field_name.startswith("res."):
        cls_name, _, param = field_name.removeprefix("res.").partition(".")
        if not cls_name or not param:
            return None, f"invalid placeholder {{{field_name}}}"
        resource = ctx.resources_by_class.get(cls_name)
        if resource is None:
            return None, f"needs {cls_name}"
        if param == "name":
            return resource.name, None
        value = resource.params.get(param)
        if value is None:
            return None, f"needs {cls_name}.{param}"
        return str(value), None
    return None, f"unknown placeholder {{{field_name}}}"


def parse_template(
    command: str,
) -> tuple[list[tuple[str, str | None, str | None, str | None]] | None, str | None]:
    """Parse *command* as a ``str.format``-style template. Returns the
    parsed ``(literal, field, spec, conversion)`` tuples, or ``(None,
    reason)`` if the brace syntax itself is malformed (unbalanced ``{``/
    ``}``): never raises. Shared by the load-time check in
    ``packs._parse_command`` and ``render_command`` so both agree on what
    counts as malformed."""
    try:
        # string.Formatter().parse() is a generator: the ValueError for
        # unbalanced braces is raised lazily while iterating, not by the
        # call itself, so the whole walk must happen inside the try.
        return list(_FORMATTER.parse(command)), None
    except ValueError:
        return None, "invalid command template"


def render_command(command: str, ctx: PlaceholderContext) -> tuple[str | None, str | None]:
    """Render every ``{placeholder}`` in *command*. Returns the rendered
    line, or ``(None, reason)`` for the first placeholder that could not
    be resolved, or a malformed template (see ``parse_template``): a
    pack file edited after registration to break its brace syntax must
    never crash rendering, which runs on every overlay open and palette
    keystroke."""
    parsed, reason = parse_template(command)
    if parsed is None:
        return None, reason
    parts: list[str] = []
    for literal_text, field_name, _format_spec, _conversion in parsed:
        parts.append(literal_text)
        if field_name is None:
            continue
        value, reason = _resolve_field(field_name, ctx)
        if reason is not None:
            return None, reason
        parts.append(value or "")
    return "".join(parts), None


def _requires_reason(
    requires: frozenset[str],
    held: bool,
    resources_by_class: dict[str, Resource],
    tags: dict[str, str],
) -> str | None:
    """First unmet item in *requires*, in a stable order so the reported
    reason does not depend on set iteration order."""
    for item in sorted(requires):
        if item == "held":
            if not held:
                return "hold the place first"
        elif item.startswith("res."):
            cls_name = item.removeprefix("res.")
            if cls_name not in resources_by_class:
                return f"needs {cls_name}"
        elif item.startswith("tag."):
            key = item.removeprefix("tag.")
            if key not in tags:
                return f"needs tag {key}"
    return None


def evaluate_pack(
    pack: Pack,
    place: Place,
    resources: list[Resource],
    me: str,
    reservations: list[Reservation] | None,
    coordinator: str,
    prefix: str,
) -> list[CommandEntry]:
    """Copy-only entries for one pack against one place. Every resulting
    ``CommandTemplate.copy_only`` is True; category is *pack.name*, which
    becomes the command overlay tab and palette group."""
    ctx = build_context(place, resources, me, reservations, coordinator, prefix)
    held = _held_by_me(place, me, reservations)
    entries: list[CommandEntry] = []
    for cmd in pack.commands:
        template = CommandTemplate(
            category=pack.name,
            label=cmd.label,
            cli_suffix=cmd.command,
            copy_only=True,
        )
        reason = _requires_reason(cmd.requires, held, ctx.resources_by_class, ctx.tags)
        rendered: str | None = None
        if reason is None:
            rendered, reason = render_command(cmd.command, ctx)
        if reason is not None:
            entries.append(CommandEntry(template, cmd.command, EntryState.UNAVAILABLE, reason))
        else:
            entries.append(CommandEntry(template, rendered or "", EntryState.RUNNABLE, None))
    return entries
