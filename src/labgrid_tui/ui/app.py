"""Textual application shell: owns the wire layer, store, and screens."""

import contextlib
import logging
import os
import time
from collections.abc import Callable, Coroutine
from dataclasses import replace
from typing import Any

from textual.app import App
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import Screen
from textual.worker import Worker

from labgrid_tui.config import Config
from labgrid_tui.coordinator.client import CoordinatorClient, CoordinatorError
from labgrid_tui.coordinator.models import Reservation, resource_key
from labgrid_tui.coordinator.source import FleetSource, GrpcFleetSource
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    EventStream,
    PlaceChanged,
    PlaceDeleted,
    ResourceChanged,
    ResourceDeleted,
    RetryScheduled,
)
from labgrid_tui.coordinators import CoordinatorError as RegistryError
from labgrid_tui.coordinators import (
    Coordinators,
    default_coordinators_path,
    load_coordinators,
    save_coordinators,
)
from labgrid_tui.model.commands import CommandEntry, CommandTemplate, default_prefix
from labgrid_tui.model.events import (
    KIND_ACQUIRED,
    KIND_DELETED,
    KIND_RELEASED,
    KIND_RESERVATION,
    KIND_RESOURCE_DELETED,
    KIND_RESOURCE_OFFLINE,
    KIND_RESOURCE_ONLINE,
    Kind,
)
from labgrid_tui.model.packs import Pack
from labgrid_tui.packs import PackError, default_packs_path, load_registered_packs, load_registry
from labgrid_tui.plugins import PluginData, load_plugins
from labgrid_tui.ui.actions import ActionRunner, CliActionRunner
from labgrid_tui.ui.layout import HORIZONTAL_BREAKPOINTS, VERTICAL_BREAKPOINTS
from labgrid_tui.ui.palette import CommandProvider
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.connection_overlay import ConnectionOverlay
from labgrid_tui.ui.screens.dashboard import DashboardScreen, TourHook
from labgrid_tui.ui.store import FleetStore
from labgrid_tui.ui.uistate import UiState, load_state, save_state, state_path
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.status_bar import SegmentProvider

logger = logging.getLogger(__name__)

RESERVATION_POLL_SECONDS = 10.0
FLEET_REFRESH_INTERVAL = 0.2
CONNECTION_GRACE_SECONDS = 5.0


class FleetEvent(Message):
    def __init__(self, event: Event) -> None:
        super().__init__()
        self.event = event


class LabgridTuiApp(App[None]):
    TITLE = "labgrid-tui"
    SUB_TITLE = "Device Dashboard"
    COMMANDS = App.COMMANDS | {CommandProvider}

    # Applied to whichever screen is active (Screen._on_resize) on every
    # resize; every screen in this app, dashboard and every modal overlay,
    # picks up -narrow/-normal/-wide and -short from here unless it sets
    # its own HORIZONTAL_BREAKPOINTS/VERTICAL_BREAKPOINTS.
    HORIZONTAL_BREAKPOINTS = HORIZONTAL_BREAKPOINTS
    VERTICAL_BREAKPOINTS = VERTICAL_BREAKPOINTS

    # Bound at app level (not just on DashboardScreen) so they also work
    # while a modal without its own binding for the key is on top, e.g. the
    # connection-lost overlay.
    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("question_mark", "toggle_help", "Help", show=True, key_display="?"),
        # Replaces the inherited ctrl+c->help_quit binding (which only prints
        # a hint); priority=True so it also reaches every ModalScreen, the
        # same way Textual's own priority ctrl+q->quit does.
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        # Vim/fzf muscle memory for the command palette, alongside Textual's
        # own ctrl+p. Non-priority: App-level BINDINGS don't reach a
        # ModalScreen, so DetailOverlay and HelpOverlay mirror this key
        # themselves (see their own BINDINGS); CommandOverlay does not,
        # because colon is a filter character there. The action must not be
        # named "command_palette": Textual only auto-registers its priority
        # ctrl+p binding when no App binding targets that action.
        Binding("colon", "open_palette", "Palette", show=False),
    ]

    def __init__(
        self,
        config: Config,
        *,
        coordinators: Coordinators | None = None,
        persist_coordinators: bool = True,
        packs: list[Pack] | None = None,
        pack_errors: list[str] | None = None,
        ui_state: UiState | None = None,
        persist_ui_state: bool = True,
        fleet_source_factory: Callable[[str], FleetSource] | None = None,
        runner_factory: Callable[["LabgridTuiApp"], ActionRunner] | None = None,
        sub_title: str | None = None,
        tour_hook: TourHook | None = None,
        on_coordinator_switch: Callable[[str], None] | None = None,
    ) -> None:
        """Every keyword-only argument is a dependency-injection seam for a
        downstream shell (or the built-in tour, see labgrid_tui.tour): left
        at its default, behavior is unchanged from a bare ``LabgridTuiApp(config)``.
        """
        super().__init__()
        if sub_title is not None:
            self.sub_title = sub_title
        self.config = config
        self._ui_state = ui_state if ui_state is not None else load_state(state_path(os.environ))
        self._persist_ui_state_enabled = persist_ui_state
        self.coordinators_path = default_coordinators_path(os.environ)
        if coordinators is not None:
            self.coordinators: Coordinators = coordinators
        else:
            try:
                self.coordinators = load_coordinators(self.coordinators_path)
            except RegistryError:
                # Same tolerance as every other machine-local file this app
                # reads: a corrupt coordinators.toml must not block startup,
                # only fall back to an empty registry (the TUI already
                # connected via Config.coordinator regardless of this file).
                self.coordinators = Coordinators()
        self.persist_coordinators = persist_coordinators
        # A theme name from a stale ui.toml may no longer be registered (renamed
        # theme, downgraded textual); falling through to the built-in default
        # is safer than crashing before the TUI starts.
        if self._ui_state.theme and self._ui_state.theme in self.available_themes:
            self.theme = self._ui_state.theme
        self.store = FleetStore()
        self.prefix = config.prefix or default_prefix(config.coordinator)
        self.plugin_data: PluginData = load_plugins()
        # config wins over plugins; both win over built-ins via
        # capabilities_for's merge.
        self.capability_extra: dict[str, str] = {
            **self.plugin_data.capabilities,
            **config.capability_overrides,
        }
        # Command templates from plugins and config, merged per class
        # (config appends after plugins); place-level extras come from
        # config only. Screens read these instead of plugin_data directly.
        self.command_extra: dict[str, tuple[CommandTemplate, ...]] = dict(self.plugin_data.commands)
        for cls_name, templates in config.command_templates.items():
            self.command_extra[cls_name] = self.command_extra.get(cls_name, ()) + templates
        self.place_extra: tuple[CommandTemplate, ...] = config.place_templates
        self.packs_path = default_packs_path(os.environ)
        if packs is not None:
            self.packs, self.pack_errors = packs, list(pack_errors or [])
        else:
            self.packs, self.pack_errors = self._load_packs()
        self.runner: ActionRunner = (
            runner_factory(self)
            if runner_factory is not None
            else CliActionRunner(
                copy_to_clipboard=self.copy_to_clipboard,
                spawn=self._spawn_worker,
                activity=self.push_event,
                output=self._emit_output,
                notify=lambda message: self.notify(message, severity="warning"),
            )
        )
        self._fleet_source_factory: Callable[[str], FleetSource] = (
            fleet_source_factory if fleet_source_factory is not None else GrpcFleetSource
        )
        self._tour_hook = tour_hook
        self._on_coordinator_switch = on_coordinator_switch
        # Created in on_mount, not here: grpc.aio binds its channel to the
        # event loop that is current at construction time, and __init__ runs
        # before app.run() starts Textual's loop. A source built here would
        # attach every RPC to the wrong loop.
        self.fleet_source: FleetSource | None = None
        # The Worker running fleet_source.start(), tracked so a coordinator
        # switch can cancel and await it explicitly: FleetSource.stop() alone
        # only stops the *next* reconnect attempt, it does not interrupt an
        # in-flight gRPC stream, so a leak-free switch needs both.
        self._stream_worker: Worker[None] | None = None
        self._fleet_dirty = False
        self._conn_timer_pending = False
        # An empty store.reservations at startup is indistinguishable from
        # "coordinator genuinely has none"; this flag lets the first poll
        # seed the store silently instead of logging every reservation
        # that already existed before the TUI connected as newly appeared.
        self._reservations_seeded = False
        # Last RetryScheduled/ConnectionChanged(CONNECTING) seen, so an overlay
        # pushed between retry cycles (_maybe_show_connection_overlay) can seed
        # its display instead of showing the static placeholder for up to a
        # full backoff interval. _last_conn_deadline is the monotonic time the
        # retry fires, tracked separately since the event's own delay is fixed
        # at emission time and goes stale as the wait progresses.
        self._last_conn_event: Event | None = None
        self._last_conn_deadline: float | None = None
        # Populated by a downstream shell wanting extra status-bar segments
        # (spec 3.2); DashboardScreen reads this via getattr.
        self.extra_status_segments: list[SegmentProvider] = []

    @property
    def client(self) -> CoordinatorClient | None:
        """Back-compat accessor: the gRPC client behind ``fleet_source`` when
        connected to a real coordinator, ``None`` otherwise (e.g. the tour)."""
        return self.fleet_source.client if isinstance(self.fleet_source, GrpcFleetSource) else None

    @property
    def stream(self) -> EventStream | None:
        source = self.fleet_source
        return source.stream if isinstance(source, GrpcFleetSource) else None

    def _on_stream_event(self, event: Event) -> None:
        self.post_message(FleetEvent(event))

    def get_default_screen(self) -> Screen[None]:
        # App.query_one() resolves against the screen present at first
        # compose, not the active screen: push_screen() in on_mount is too
        # late for #fleet-table etc. to be queryable from the app. This hook
        # runs at compose time, making DashboardScreen the bottom-of-stack
        # default screen instead.
        return DashboardScreen(
            self.runner, self._ui_state, self._persist_ui_state, tour_hook=self._tour_hook
        )

    def _persist_ui_state(self) -> None:
        if not self._persist_ui_state_enabled:
            return
        save_state(state_path(os.environ), self._ui_state)

    def _load_packs(self) -> tuple[list[Pack], list[str]]:
        """Every registered command pack that parsed, plus one error
        string per pack that didn't: a missing or broken pack is
        skipped, never fatal to startup (see README.md (Command packs))."""
        errors: list[str] = []
        try:
            registry = load_registry(self.packs_path)
        except PackError as exc:
            return [], [str(exc)]
        packs: list[Pack] = []
        for loaded in load_registered_packs(registry):
            if loaded.pack is None:
                errors.append(f"pack {loaded.entry.name}: " + "; ".join(loaded.errors))
                continue
            if loaded.errors:
                errors.append(f"pack {loaded.entry.name}: " + "; ".join(loaded.errors))
            packs.append(loaded.pack)
        return packs, errors

    def watch_theme(self, _old: str, new: str) -> None:
        self._ui_state.theme = new
        self._persist_ui_state()

    def _connect(self, address: str) -> None:
        # grpc.aio binds a channel to the event loop current when it's
        # created; called only from on_mount and _do_switch_coordinator, both
        # of which run on Textual's own running loop, never from __init__.
        self.fleet_source = self._fleet_source_factory(address)
        self._stream_worker = self.run_worker(
            self.fleet_source.start(self._on_stream_event), exclusive=False
        )

    def on_mount(self) -> None:
        self._connect(self.config.coordinator)
        self.set_interval(RESERVATION_POLL_SECONDS, self._poll_reservations)
        self.set_interval(FLEET_REFRESH_INTERVAL, self._flush_fleet_refresh)
        if self.config.config_error:
            # Deferred: the default screen is on screen_stack by this point
            # (see get_default_screen), but its compose(), and so
            # ActivityLog, which push_activity queries for, hasn't run
            # yet. call_after_refresh waits for that to settle.
            self.call_after_refresh(self._report_config_error)
        if self.pack_errors:
            self.call_after_refresh(self._report_pack_errors)
        if not self._ui_state.onboarded:
            self.set_timer(
                0.5,
                lambda: self.notify(
                    "Press [bold]?[/] for help · [bold]Ctrl+P[/] for commands",
                    title="Welcome to labgrid-tui",
                    timeout=8,
                ),
            )
            self.set_timer(
                4.0,
                lambda: self.notify(
                    "[bold]↑↓[/] navigate"
                    " · [bold]r[/] acquire"
                    " · [bold]Space[/] mark"
                    " · [bold]c[/] commands",
                    timeout=8,
                ),
            )
            self._ui_state.onboarded = True
            self._persist_ui_state()

    def _report_config_error(self) -> None:
        error = self.config.config_error
        if error is None:
            return
        self.push_activity(f"config ignored: {error}")
        self.notify(
            f"{error}\nrun: labgrid-tui config show",
            title="Config error",
            severity="warning",
            timeout=8,
        )

    def _report_pack_errors(self) -> None:
        for error in self.pack_errors:
            self.push_activity(f"pack ignored: {error}")
        count = len(self.pack_errors)
        self.notify(
            f"{count} command pack{'' if count == 1 else 's'} failed to load"
            "\nrun: labgrid-tui pack list",
            title="Command pack error",
            severity="warning",
            timeout=8,
        )

    def on_fleet_event(self, message: FleetEvent) -> None:
        # refresh_fleet costs O(places x resources) via resources_of, and
        # reconnect replays deliver a whole fleet's worth of events as one
        # burst. Mark dirty here and let the interval in _flush_fleet_refresh
        # coalesce the burst into a single refresh instead of one per event.
        event = message.event
        self._log_activity(event)
        self.store.apply(event)
        self._fleet_dirty = True
        if isinstance(event, RetryScheduled):
            self._forward_to_overlay(event)
        elif isinstance(event, ConnectionChanged):
            if event.state is ConnState.LIVE:
                self._dismiss_connection_overlays()
                self._conn_timer_pending = False
                self._last_conn_event = None
                self._last_conn_deadline = None
            else:
                if event.state is ConnState.CONNECTING:
                    self._forward_to_overlay(event)
                if not self._conn_timer_pending:
                    self._conn_timer_pending = True
                    self.set_timer(CONNECTION_GRACE_SECONDS, self._maybe_show_connection_overlay)

    def _dismiss_connection_overlays(self) -> None:
        # A screen pushed after the overlay (e.g. the built-in command
        # palette, reachable via an App-level binding even while a modal is
        # active) buries it below the stack top; Screen.dismiss() always
        # pops whatever is currently on top, so a buried overlay must be
        # unmounted directly instead.
        for screen in self.screen_stack:
            if isinstance(screen, ConnectionOverlay):
                if screen is self.screen:
                    screen.dismiss()
                else:
                    self._screen_stack.remove(screen)
                    screen.remove()

    def _forward_to_overlay(self, event: Event) -> None:
        self._last_conn_event = event
        self._last_conn_deadline = (
            time.monotonic() + event.delay if isinstance(event, RetryScheduled) else None
        )
        for screen in self.screen_stack:
            if isinstance(screen, ConnectionOverlay):
                screen.apply_stream_event(event)

    def _current_conn_event(self) -> Event | None:
        """Snapshot of the last retry/connecting event with its delay
        corrected for elapsed time, so a freshly pushed overlay can seed
        its countdown from "now" instead of restarting from the original
        RetryScheduled delay."""
        event = self._last_conn_event
        if isinstance(event, RetryScheduled) and self._last_conn_deadline is not None:
            remaining = max(0.0, self._last_conn_deadline - time.monotonic())
            return replace(event, delay=remaining)
        return event

    def _maybe_show_connection_overlay(self) -> None:
        self._conn_timer_pending = False
        if self.store.conn is ConnState.LIVE or isinstance(self.screen, ConnectionOverlay):
            return
        self.push_screen(
            ConnectionOverlay(
                self.config.coordinator,
                self.config.coordinator_source,
                initial_event=self._current_conn_event(),
            )
        )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # Textual's command-palette binding is priority=True at the App
        # level, so it is checked (and would win) before any ModalScreen's
        # own bindings for the same key. CommandOverlay reassigns ctrl+p to
        # option-list navigation (fzf/vim completion keys); disabling the
        # palette action here while it is on top lets that screen-level
        # binding be reached instead; the palette is deliberately not
        # reachable from inside CommandOverlay (close it first).
        return not (action == "command_palette" and isinstance(self.screen, CommandOverlay))

    def action_open_palette(self) -> None:
        self.action_command_palette()

    def action_toggle_help(self) -> None:
        for screen in self.screen_stack:
            if isinstance(screen, DashboardScreen):
                screen.action_toggle_help()
                return

    def retry_connection(self) -> None:
        if self.fleet_source is not None:
            self.fleet_source.retry_now()

    def switch_coordinator(self, name: str) -> None:
        """Reconnect to a different coordinators.toml entry.

        Runs in a worker (it's async: closing the old channel and awaiting
        its stream task requires it) so the caller, a modal's dismiss
        callback, can stay a plain synchronous method.
        """
        self.run_worker(
            self._do_switch_coordinator(name), exclusive=True, group="coordinator-switch"
        )

    async def _do_switch_coordinator(self, name: str) -> None:
        entry = self.coordinators.entries.get(name)
        if entry is None:
            return
        old_source, old_worker = self.fleet_source, self._stream_worker
        if old_source is not None:
            old_source.stop()
        if old_worker is not None:
            # stop() alone only cancels the *next* reconnect wait; an
            # in-flight gRPC stream needs the Task itself cancelled so it
            # doesn't keep delivering events (and holding the old channel
            # open) after the store below has been reset for the new one.
            old_worker.cancel()
            with contextlib.suppress(Exception):
                await old_worker.wait()
        if old_source is not None:
            await old_source.aclose()

        self.store = FleetStore()
        self._reservations_seeded = False
        self._conn_timer_pending = False
        self._last_conn_event = None
        self._last_conn_deadline = None
        self._dismiss_connection_overlays()
        with contextlib.suppress(NoMatches):
            self.screen_stack[0].query_one(DeviceTable).marks.clear()

        self.coordinators.current = entry.name
        if self.persist_coordinators:
            with contextlib.suppress(RegistryError):
                save_coordinators(self.coordinators_path, self.coordinators)

        self.config = replace(
            self.config,
            coordinator=entry.address,
            coordinator_source=f"coordinator {entry.name}",
            coordinator_prefix=entry.prefix,
        )
        self.prefix = self.config.prefix or entry.prefix or default_prefix(entry.address)

        self._connect(entry.address)
        self._fleet_dirty = True
        self.push_activity(f"coordinator switched to {entry.name} ({entry.address})")
        if self._on_coordinator_switch is not None:
            self._on_coordinator_switch(entry.name)

    def _log_activity(self, event: Event) -> None:
        # Coordinator replay on (re)connect resends every place/resource as
        # if newly changed; suppress activity lines while CONNECTING so
        # startup and reconnects stay quiet instead of spamming the log.
        if self.store.conn is ConnState.CONNECTING:
            return
        match event:
            case PlaceChanged(place=place):
                old = self.store.places.get(place.name)
                old_acquired = old.acquired if old else None
                if old_acquired == place.acquired:
                    return
                if old_acquired is None:
                    self.push_event(KIND_ACQUIRED, place.name, f"acquired by {place.acquired}")
                elif place.acquired is None:
                    self.push_event(KIND_RELEASED, place.name, f"released by {old_acquired}")
                else:
                    self.push_event(KIND_ACQUIRED, place.name, f"acquired by {place.acquired}")
            case ResourceChanged(resource=resource):
                old_resource = self.store.resources.get(resource_key(resource))
                old_avail = old_resource.avail if old_resource else None
                if old_avail == resource.avail:
                    return
                if old_resource is None and resource.avail:
                    return  # first sight of an online resource: not noteworthy
                subject = f"{resource.exporter}/{resource.group}/{resource.name}"
                if resource.avail:
                    self.push_event(KIND_RESOURCE_ONLINE, subject, "online")
                else:
                    self.push_event(KIND_RESOURCE_OFFLINE, subject, "offline")
            case PlaceDeleted(name=name):
                self.push_event(KIND_DELETED, name, "removed")
            case ResourceDeleted(exporter=exporter, group=group, name=name):
                self.push_event(KIND_RESOURCE_DELETED, f"{exporter}/{group}/{name}", "removed")

    def _flush_fleet_refresh(self) -> None:
        if not self._fleet_dirty:
            return
        self._fleet_dirty = False
        for screen in self.screen_stack:
            refresh = getattr(screen, "refresh_fleet", None)
            if refresh is not None:
                refresh()

    async def _poll_reservations(self) -> None:
        if self.fleet_source is None:
            return
        try:
            reservations = await self.fleet_source.get_reservations()
        except CoordinatorError as exc:
            logger.debug("reservation poll failed: %s", exc)
            return
        except Exception:
            # A failed poll must never take the app down; the fleet view
            # keeps working from the stream regardless.
            logger.exception("unexpected error in reservation poll")
            return
        if self._reservations_seeded:
            self._log_reservation_changes(reservations)
        self._reservations_seeded = True
        self.store.reservations = reservations

    def _log_reservation_changes(self, reservations: list[Reservation]) -> None:
        # No push event exists for reservation state on the wire (unlike
        # places/resources), so state transitions are only observable by
        # diffing successive polls, keyed by token.
        old_by_token = {r.token: r for r in self.store.reservations}
        new_by_token = {r.token: r for r in reservations}
        for token, reservation in new_by_token.items():
            old = old_by_token.get(token)
            if old is None:
                suffix = self._allocated_suffix(reservation)
                detail = f"reservation {token} {reservation.state.name}{suffix}"
                self.push_event(KIND_RESERVATION, reservation.owner, detail)
            elif old.state != reservation.state:
                self.push_event(
                    KIND_RESERVATION,
                    reservation.owner,
                    f"reservation {token} -> {reservation.state.name}"
                    f"{self._allocated_suffix(reservation)}",
                )
        for token, reservation in old_by_token.items():
            if token not in new_by_token:
                self.push_event(KIND_RESERVATION, reservation.owner, f"reservation {token} gone")

    @staticmethod
    def _allocated_suffix(reservation: Reservation) -> str:
        # allocations is keyed by filter group name ("main" is the only one
        # the coordinator implements); show which place the user got.
        place = reservation.allocations.get("main")
        return f" ({place})" if place else ""

    def push_activity(self, line: str) -> None:
        screen = self.screen_stack[0]
        log_line = getattr(screen, "log_line", None)
        if log_line is not None:
            log_line(line)

    def push_event(self, kind: Kind, subject: str, detail: str) -> None:
        screen = self.screen_stack[0]
        log_event = getattr(screen, "log_event", None)
        if log_event is not None:
            log_event(kind, subject, detail)

    def copy_entry(self, entry: CommandEntry) -> None:
        self.runner.copy(entry)

    def run_entry(self, entry: CommandEntry) -> None:
        self.runner.run(entry)

    def _emit_output(self, line: str) -> None:
        for screen in self.screen_stack:
            log_output = getattr(screen, "log_output", None)
            if log_output is not None:
                log_output(line)

    def _spawn_worker(self, coro: Coroutine[Any, Any, None]) -> None:
        self.run_worker(coro, exclusive=False)

    async def on_unmount(self) -> None:
        if self.fleet_source is not None:
            self.fleet_source.stop()
            await self.fleet_source.aclose()
