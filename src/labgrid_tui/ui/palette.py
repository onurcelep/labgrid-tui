"""Command-palette provider: global reservation commands plus dashboard-
contextual actions, available from any screen.

Global entries (filter, activity log, help), device-contextual entries for
the cursor place (acquire or release, show commands, detail), bulk entries
when marks exist, and per-place CLI copy entries flattened from
``evaluate()``. ``discover()`` shows everything except the CLI copy
entries (too many to be useful as a default list); ``search()``
fuzzy-matches the full set.
"""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.css.query import NoMatches

from labgrid_tui.model.commands import (
    GLOBAL_TEMPLATES,
    CommandEntry,
    EntryState,
    evaluate,
    render_global,
    reservation_entries,
)
from labgrid_tui.model.identity import current_id
from labgrid_tui.model.packs import evaluate_pack
from labgrid_tui.ui.screens.dashboard import DashboardScreen
from labgrid_tui.ui.widgets.device_table import DeviceTable


@dataclass(frozen=True)
class _Command:
    """Internal representation of a palette command."""

    name: str
    help: str | None
    callback: Callable[[], object]


class CommandProvider(Provider):
    async def discover(self) -> Hits:
        """Yield default commands shown before the user types anything."""
        for cmd in self._commands(include_cli=False):
            yield DiscoveryHit(display=cmd.name, command=cmd.callback, help=cmd.help)

    async def search(self, query: str) -> Hits:
        """Fuzzy-match *query* against all available commands."""
        matcher = self.matcher(query)
        for cmd in self._commands(include_cli=True):
            score = matcher.match(cmd.name)
            if score > 0:
                yield Hit(score, matcher.highlight(cmd.name), cmd.callback, help=cmd.help)

    # ------------------------------------------------------------------
    # Command builders
    # ------------------------------------------------------------------

    def _commands(self, *, include_cli: bool) -> list[_Command]:
        cmds = self._reservation_commands() + self._global_commands() + self._device_commands()
        cmds += self._my_reservation_commands()
        cmds += self._bulk_commands()
        cmds += self._coordinator_commands()
        if include_cli:
            cmds += self._cli_commands()
            cmds += self._pack_commands()
        return cmds

    def _coordinator_commands(self) -> list[_Command]:
        """Coordinator switch and manage entries: one "switch to NAME" per
        non-current entry, plus "manage" to open the selector: available
        from any screen the same way the reservation commands are."""
        dashboard = self._dashboard()
        if dashboard is None:
            return []
        coordinators = getattr(self.app, "coordinators", None)
        if coordinators is None:
            return []
        cmds: list[_Command] = [
            _Command(
                "Coordinator: manage",
                "Open the coordinator selector",
                dashboard.action_switch_coordinator,
            )
        ]
        for name in sorted(coordinators.entries):
            if name == coordinators.current:
                continue
            switch = getattr(self.app, "switch_coordinator", None)
            if switch is None:
                continue
            cmds.append(
                _Command(
                    f"Coordinator: switch to {name}",
                    coordinators.entries[name].address,
                    partial(switch, name),
                )
            )
        return cmds

    def _reservation_commands(self) -> list[_Command]:
        """Copy-only global reservation commands (no place context)."""
        prefix = getattr(self.app, "prefix", None)
        if prefix is None:
            return []
        cmds: list[_Command] = []
        for template in GLOBAL_TEMPLATES:
            command_line = render_global(template, prefix)
            entry = CommandEntry(template, command_line, EntryState.RUNNABLE, None)
            cmds.append(
                _Command(
                    f"{template.label} {command_line}",
                    command_line,
                    partial(self._copy_entry, entry),
                )
            )
        return cmds

    def _my_reservation_commands(self) -> list[_Command]:
        """Copy entries for the user's own reservations (cancel it, or
        acquire the place it was allocated), independent of the cursor
        place: unlike ``_cli_commands()``, available even when nothing is
        selected or the cursor sits on an unrelated place."""
        store = getattr(self.app, "store", None)
        prefix = getattr(self.app, "prefix", None)
        if store is None or prefix is None:
            return []
        entries = reservation_entries(store.reservations, current_id(), prefix)
        cmds: list[_Command] = []
        for entry in entries:
            help_text = entry.command_line
            if entry.state is not EntryState.RUNNABLE:
                help_text = f"{help_text} (unavailable)"
            cmds.append(
                _Command(
                    f"{entry.template.label} {entry.command_line}",
                    help_text,
                    partial(self._copy_entry, entry),
                )
            )
        return cmds

    def _global_commands(self) -> list[_Command]:
        """Commands available regardless of place selection."""
        cmds: list[_Command] = []
        dashboard = self._dashboard()

        if dashboard is not None:
            cmds.append(
                _Command(
                    "Filter: Search places",
                    "Open the filter bar to search by name, comment, tags, or capabilities",
                    dashboard.action_show_filter,
                )
            )
            cmds.append(
                _Command(
                    "View: Toggle activity log",
                    "Show or hide the activity log",
                    dashboard.action_toggle_activity,
                )
            )

        app_toggle_help = getattr(self.app, "action_toggle_help", None)
        if app_toggle_help is not None:
            cmds.append(
                _Command(
                    "Help: Toggle help panel",
                    "Show the help panel (?)",
                    app_toggle_help,
                )
            )

        return cmds

    def _device_commands(self) -> list[_Command]:
        """Commands that depend on the cursor place."""
        dashboard = self._dashboard()
        if dashboard is None:
            return []
        table = self._device_table(dashboard)
        if table is None:
            return []
        place_name = table.cursor_place()
        if place_name is None:
            return []
        store = getattr(self.app, "store", None)
        place = store.places.get(place_name) if store is not None else None
        if place is None:
            return []

        cmds: list[_Command] = []
        if bool(place.acquired) or bool(place.reservation):
            cmds.append(
                _Command(
                    f"Device: Release {place_name}",
                    "Release the selected place",
                    dashboard.action_release,
                )
            )
        else:
            cmds.append(
                _Command(
                    f"Device: Acquire {place_name}",
                    "Acquire the selected place",
                    dashboard.action_acquire,
                )
            )
        cmds.append(
            _Command(
                f"Device: Show commands for {place_name}",
                "Show copyable CLI commands for the selected place",
                dashboard.action_commands,
            )
        )
        cmds.append(
            _Command(
                f"Device: Detail for {place_name}",
                "Show full detail overlay for the selected place",
                dashboard.action_toggle_detail,
            )
        )
        return cmds

    def _bulk_commands(self) -> list[_Command]:
        """Bulk verb commands when the table has marked places."""
        dashboard = self._dashboard()
        if dashboard is None:
            return []
        table = self._device_table(dashboard)
        if table is None or not table.marks:
            return []
        count = len(table.marks)
        return [
            _Command(
                f"Marks: Acquire all ({count} marked)",
                "Acquire all marked places",
                partial(dashboard._dispatch_verb, "Acquire"),
            ),
            _Command(
                f"Marks: Release all ({count} marked)",
                "Release all marked places",
                partial(dashboard._dispatch_verb, "Release"),
            ),
        ]

    def _cli_commands(self) -> list[_Command]:
        """CLI copy entries for the cursor place, flattened from
        ``evaluate()``. Only shown once the place is held (acquired or
        reserved): a free place offers no resource commands at all."""
        dashboard = self._dashboard()
        if dashboard is None:
            return []
        table = self._device_table(dashboard)
        if table is None:
            return []
        place_name = table.cursor_place()
        if place_name is None:
            return []
        store = getattr(self.app, "store", None)
        if store is None:
            return []
        place = store.places.get(place_name)
        if place is None or not (bool(place.acquired) or bool(place.reservation)):
            return []

        resources = store.resources_of(place)
        prefix = getattr(self.app, "prefix", "labgrid-client")
        entries = evaluate(
            place,
            resources,
            current_id(),
            prefix,
            extra_templates=getattr(self.app, "command_extra", None),
            extra_place=getattr(self.app, "place_extra", None),
            reservations=store.reservations,
        )
        cmds: list[_Command] = []
        for entry in entries:
            if entry.template.category == "Reservations":
                # Independent of the cursor place; surfaced once by
                # _my_reservation_commands() instead of once per place here.
                continue
            display = f"CLI: {entry.template.label} {place_name}"
            help_text = entry.command_line
            if entry.state is not EntryState.RUNNABLE:
                help_text = f"{help_text} (unavailable)"
            cmds.append(_Command(display, help_text, partial(self._copy_entry, entry)))
        return cmds

    def _pack_commands(self) -> list[_Command]:
        """Command pack entries for the cursor place, one "PACK: Label"
        per command. Independent of held/acquired state, unlike
        ``_cli_commands()``: each entry's own gating decides
        availability (see model.packs.evaluate_pack)."""
        dashboard = self._dashboard()
        if dashboard is None:
            return []
        table = self._device_table(dashboard)
        if table is None:
            return []
        place_name = table.cursor_place()
        if place_name is None:
            return []
        store = getattr(self.app, "store", None)
        packs = getattr(self.app, "packs", None)
        config = getattr(self.app, "config", None)
        prefix = getattr(self.app, "prefix", None)
        if store is None or not packs or config is None or prefix is None:
            return []
        place = store.places.get(place_name)
        if place is None:
            return []

        resources = store.resources_of(place)
        me = current_id()
        cmds: list[_Command] = []
        for pack in packs:
            entries = evaluate_pack(
                pack, place, resources, me, store.reservations, config.coordinator, prefix
            )
            for entry in entries:
                display = f"{pack.name}: {entry.template.label}"
                help_text = entry.command_line
                if entry.state is not EntryState.RUNNABLE:
                    help_text = f"{help_text} (unavailable: {entry.reason})"
                cmds.append(_Command(display, help_text, partial(self._copy_entry, entry)))
        return cmds

    # ------------------------------------------------------------------
    # State accessors
    # ------------------------------------------------------------------

    def _dashboard(self) -> DashboardScreen | None:
        """Walk the screen stack to find the DashboardScreen, if any."""
        for screen in self.app.screen_stack:
            if isinstance(screen, DashboardScreen):
                return screen
        return None

    def _device_table(self, dashboard: DashboardScreen) -> DeviceTable | None:
        try:
            return dashboard.query_one(DeviceTable)
        except NoMatches:
            return None

    # ------------------------------------------------------------------
    # Callback
    # ------------------------------------------------------------------

    def _copy_entry(self, entry: CommandEntry) -> None:
        # Route through the app's ActionRunner so every copy toasts and logs
        # to the activity log the same way, whether it came from the palette,
        # the command overlay, or a verb key.
        copy_entry = getattr(self.app, "copy_entry", None)
        if copy_entry is not None:
            copy_entry(entry)
