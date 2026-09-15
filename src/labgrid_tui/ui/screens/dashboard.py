"""Composed verb-driven dashboard: fleet table, detail overlay, activity log."""

from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.geometry import Size
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, Static

import labgrid_tui
from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.coordinators import (
    CoordinatorEntry,
    CoordinatorError,
    save_coordinators,
    validate_coordinator,
    validate_name,
)
from labgrid_tui.exec_.runner import client_available
from labgrid_tui.model.commands import CommandEntry, EntryState, evaluate, is_verb
from labgrid_tui.model.events import Kind
from labgrid_tui.model.identity import current_id
from labgrid_tui.model.packs import evaluate_pack
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.layout import MIN_HEIGHT, MIN_WIDTH, is_narrow, too_small
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.coordinator_delete import CoordinatorDeleteConfirm
from labgrid_tui.ui.screens.coordinator_edit import CoordinatorEditModal, FieldSpec
from labgrid_tui.ui.screens.coordinator_selector import (
    CoordinatorSelector,
    CoordinatorSelectorResult,
    CreateResult,
    DeleteResult,
    EditResult,
    SwitchResult,
)
from labgrid_tui.ui.screens.detail_overlay import DetailOverlay
from labgrid_tui.ui.screens.help_overlay import HelpOverlay
from labgrid_tui.ui.uistate import UiState
from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.filter_bar import FilterBar
from labgrid_tui.ui.widgets.status_bar import (
    PRIORITY_COPY_ONLY,
    PRIORITY_COUNTS,
    PRIORITY_EXTRA,
    PRIORITY_SOURCE_SUFFIX,
    PRIORITY_VERSION,
    Segment,
    StatusBar,
)

if TYPE_CHECKING:
    # Only for the cast in _persist_coordinators below: importing
    # LabgridTuiApp for real would be circular (app.py imports this
    # module). The cast makes app.persist_coordinators a real,
    # mypy --strict-checked attribute access instead of a getattr(...,
    # default=True) that would silently re-enable writes if it were ever
    # renamed on LabgridTuiApp without this call site following along.
    from labgrid_tui.ui.app import LabgridTuiApp

_CONN_LABEL = {
    ConnState.CONNECTING: "connecting...",
    ConnState.LIVE: "live",
    ConnState.DISCONNECTED: "reconnecting (data stale)",
}

# (event name, detail) -> the tour's step sequencer, e.g. ("detail_open",
# place_name). See labgrid_tui.tour.steps.TourController.
TourHook = Callable[[str, str], None]


class DashboardScreen(Screen[None]):
    DEFAULT_CSS = """
    DashboardScreen { layout: vertical; }
    /* Header docks top on its own; nothing else may dock to the same edge:
       Textual overlays same-edge docks instead of stacking them. */
    #status-bar { height: 1; padding: 0 1; background: $panel; color: $text; }
    #filter-bar { height: auto; }
    #main-row { height: 1fr; }
    #fleet-table { height: 1fr; min-height: 5; }
    #activity-log { height: 30%; min-height: 4; max-height: 12; border-top: solid $primary; }
    #too-small-notice {
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    .hidden { display: none; }

    DashboardScreen.-short #activity-log { max-height: 5; }
    /* HeaderClock is only ever composed when Header(show_clock=True); hiding
       it here (rather than toggling the constructor flag, which Header
       does not expose as a runtime-settable reactive) frees its docked
       width for the title in a narrow terminal. */
    DashboardScreen.-narrow Header HeaderClock { display: none; }
    """

    # FilterBar is the first focusable widget in compose order; without this,
    # Textual's default auto-focus grabs it on mount even while it is hidden
    # (display: none does not exclude a widget from the focus chain), which
    # silently swallows every single-key binding as literal Input text.
    AUTO_FOCUS = "DeviceTable"

    BINDINGS = [
        Binding("r", "acquire", "Acquire"),
        Binding("R", "release", "Release", key_display="Shift+R"),
        Binding("c", "commands", "Cmds"),
        Binding("p", "power_commands", "Power", show=False),
        Binding("d,enter", "toggle_detail", "Detail"),
        Binding("a", "toggle_activity", "Activity"),
        Binding("slash", "show_filter", "Filter", key_display="/"),
        Binding("y", "copy_row", "Copy", show=False),
        Binding("escape", "smart_escape", "Close", show=False),
        Binding("P", "switch_coordinator", "Coordinator", key_display="Shift+P"),
    ]

    def __init__(
        self,
        runner: ActionRunner,
        ui_state: UiState,
        persist: Callable[[], None],
        *,
        tour_hook: TourHook | None = None,
    ) -> None:
        super().__init__()
        self._runner = runner
        self._ui_state = ui_state
        self._persist = persist
        # shutil.which under the hood; the fleet-refresh interval would
        # otherwise call it up to 5x/s for a status segment that never
        # changes within a session.
        self._client_available = client_available()
        # Set only by the built-in tour (labgrid_tui.tour): a tiny sideband
        # so its step sequencer can observe dashboard actions (cursor,
        # marks, detail, commands overlay) without this screen knowing
        # anything about tour state.
        self._tour_hook = tour_hook

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatusBar(self._status_segments())
        yield FilterBar().add_class("hidden")
        with Horizontal(id="main-row"):
            yield DeviceTable()
        yield Static("", id="too-small-notice").add_class("hidden")
        log = ActivityLog()
        if not self._ui_state.show_activity:
            log.add_class("hidden")
        yield log
        yield Footer()

    def _status_segments(self) -> list[Segment]:
        app = self.app
        segments = [
            Segment(
                self._coordinator_segment,
                priority=PRIORITY_SOURCE_SUFFIX,
                degraded=self._coordinator_segment_short,
                shrinkable=True,
            ),
            Segment(self._connection_segment),
            Segment(
                self._places_segment,
                priority=PRIORITY_COUNTS,
                degraded=self._places_segment_abbreviated,
            ),
            Segment(self._marked_segment, priority=PRIORITY_COUNTS),
            Segment(self._copy_only_segment, priority=PRIORITY_COPY_ONLY),
            Segment(self._version_segment, priority=PRIORITY_VERSION),
        ]
        segments.extend(
            Segment(provider, priority=PRIORITY_EXTRA)
            for provider in getattr(app, "extra_status_segments", [])
        )
        return segments

    def _coordinator_segment(self) -> str | None:
        config = getattr(self.app, "config", None)
        if config is None:
            return None
        return f"{config.coordinator} (from {config.coordinator_source})"

    def _coordinator_segment_short(self) -> str | None:
        config = getattr(self.app, "config", None)
        if config is None:
            return None
        return str(config.coordinator)

    def _connection_segment(self) -> str | None:
        store = getattr(self.app, "store", None)
        if store is None:
            return None
        return _CONN_LABEL.get(store.conn, "unknown")

    def _places_segment(self, *, abbreviated: bool = False) -> str | None:
        store = getattr(self.app, "store", None)
        if store is None:
            return None
        total = len(store.places)
        acquired = sum(1 for p in store.places.values() if p.acquired)
        reserved = sum(1 for p in store.places.values() if p.reservation and not p.acquired)
        free = total - acquired - reserved
        segment = (
            f"{total} total | {free} free | {acquired} acq"
            if abbreviated
            else f"{total} total | {free} free | {reserved} reserved | {acquired} acquired"
        )
        try:
            table = self.query_one(DeviceTable)
        except NoMatches:
            return segment
        if table.filter_query.strip():
            segment += f" | {table.row_count} shown"
        return segment

    def _places_segment_abbreviated(self) -> str | None:
        return self._places_segment(abbreviated=True)

    def _marked_segment(self) -> str | None:
        try:
            table = self.query_one(DeviceTable)
        except NoMatches:
            return None
        if not table.marks:
            return None
        return f"marked: {len(table.marks)}"

    def _copy_only_segment(self) -> str | None:
        if self._client_available:
            return None
        return "copy-only (labgrid-client not found)"

    def _version_segment(self) -> str | None:
        return f"v{labgrid_tui.__version__}"

    def on_mount(self) -> None:
        self._update_too_small(self.size)
        self._update_footer_compact(self.app.size)
        self.refresh_fleet()

    def on_resize(self, event: events.Resize) -> None:
        self._update_too_small(event.size)
        self._update_footer_compact(event.size)

    def _update_footer_compact(self, size: Size) -> None:
        # Size-derived rather than has_class("-narrow"): this screen's own
        # on_mount/on_resize runs before Screen._on_resize applies the
        # breakpoint class (most-derived handler dispatches first), so the
        # class reads stale on a cold app start.
        self.query_one(Footer).compact = is_narrow(size)

    def _update_too_small(self, size: Size) -> None:
        notice = self.query_one("#too-small-notice", Static)
        main_row = self.query_one("#main-row", Horizontal)
        if too_small(size):
            notice.update(
                f"terminal too small (need {MIN_WIDTH}x{MIN_HEIGHT}, "
                f"have {size.width}x{size.height})"
            )
            notice.remove_class("hidden")
            main_row.add_class("hidden")
        else:
            notice.add_class("hidden")
            main_row.remove_class("hidden")

    def refresh_fleet(self) -> None:
        app = self.app
        store = getattr(app, "store", None)
        if store is None:
            return
        try:
            table = self.query_one(DeviceTable)
        except NoMatches:
            # App.push_screen appends to screen_stack before mount
            # completes; a FleetEvent landing in that window must not crash
            # querying widgets that don't exist yet. on_mount calls
            # refresh_fleet itself once mounted, so nothing is lost here.
            return
        capability_extra = getattr(app, "capability_extra", None)
        table.refresh_rows(store, capability_extra)
        self._refresh_status()

    def log_line(self, line: str) -> None:
        self.query_one(ActivityLog).log_line(line)

    def log_event(self, kind: Kind, subject: str, detail: str) -> None:
        self.query_one(ActivityLog).log_event(kind, subject, detail)

    def log_output(self, line: str) -> None:
        self.query_one(ActivityLog).log_output(line)

    def action_show_filter(self) -> None:
        filter_bar = self.query_one(FilterBar)
        filter_bar.remove_class("hidden")
        filter_bar.focus()

    def action_toggle_detail(self) -> None:
        place_name = self.query_one(DeviceTable).cursor_place()
        if place_name is None:
            self.notify("no place under the cursor")
            return
        if self._tour_hook is not None:
            self._tour_hook("detail_open", place_name)
        self.app.push_screen(
            DetailOverlay(place_name, self._runner), callback=self._on_detail_dismissed
        )

    def _on_detail_dismissed(self, _result: None) -> None:
        if self._tour_hook is not None:
            self._tour_hook("detail_close", "")

    def action_toggle_activity(self) -> None:
        log = self.query_one(ActivityLog)
        self._ui_state.show_activity = not self._ui_state.show_activity
        log.set_class(not self._ui_state.show_activity, "hidden")
        self._persist()

    def action_toggle_help(self) -> None:
        if isinstance(self.app.screen, HelpOverlay):
            self.app.pop_screen()
        else:
            self.app.push_screen(HelpOverlay())

    def action_commands(self) -> None:
        self._open_overlay(category=None)

    def action_power_commands(self) -> None:
        self._open_overlay(category="Power")

    def _open_overlay(self, *, category: str | None) -> None:
        place_name = self.query_one(DeviceTable).cursor_place()
        if place_name is None:
            self.notify("no place under the cursor")
            return
        entries = self._entries_for(place_name)
        self.app.push_screen(
            CommandOverlay(
                place_name,
                entries,
                self._runner,
                category=category,
                prefix=getattr(self.app, "prefix", None),
            )
        )

    def _entries_for(self, place_name: str) -> list[CommandEntry]:
        app = self.app
        store = getattr(app, "store", None)
        if store is None:
            return []
        place = store.places.get(place_name)
        if place is None:
            return []
        resources = store.resources_of(place)
        me = current_id()
        prefix = getattr(app, "prefix", "labgrid-client")
        entries = evaluate(
            place,
            resources,
            me,
            prefix,
            extra_templates=getattr(app, "command_extra", None),
            extra_place=getattr(app, "place_extra", None),
            reservations=store.reservations,
        )
        config = getattr(app, "config", None)
        coordinator = config.coordinator if config is not None else ""
        for pack in getattr(app, "packs", None) or ():
            entries.extend(
                evaluate_pack(pack, place, resources, me, store.reservations, coordinator, prefix)
            )
        return entries

    def action_acquire(self) -> None:
        self._dispatch_verb("Acquire")

    def action_release(self) -> None:
        self._dispatch_verb("Release")

    def _dispatch_verb(self, label: str) -> None:
        table = self.query_one(DeviceTable)
        targets = table.selected_targets()
        if not targets:
            self.app.notify(f"{label}: no target")
            return
        ran = 0
        skipped = 0
        skip_name: str | None = None
        skip_reason: str | None = None
        # A bulk dispatch posts one summary toast; per-item success toasts
        # would drown it out. Failures still toast per item either way.
        notify_success = len(targets) == 1
        for name in targets:
            # is_verb recognises the built-in verbs only (the acquire verb
            # by its requires, since its label follows the place state);
            # a pack entry can never satisfy this lookup.
            entry = next(
                (e for e in self._entries_for(name) if is_verb(e.template, label)),
                None,
            )
            if entry is None:
                # Place removed or entry no longer applicable since the target
                # was selected; counts toward skipped (ran + skipped ==
                # len(targets)) but gets no per-item activity line.
                skipped += 1
                skip_name = name
                skip_reason = None
                continue
            if entry.state is EntryState.RUNNABLE:
                self._runner.run(entry, notify_success=notify_success)
                ran += 1
            else:
                skipped += 1
                skip_name = name
                skip_reason = entry.reason
                self.log_line(f"skipped {name}: {entry.reason}")
        if len(targets) > 1:
            self.app.notify(f"{label}: {ran} run, {skipped} skipped")
            self._clear_marks(table)
        elif ran == 0 and skipped == 1:
            message = skip_reason if skip_reason is not None else f"{label}: {skip_name} is gone"
            self.app.notify(message)

    def action_copy_row(self) -> None:
        table = self.query_one(DeviceTable)
        if self.focused is not table:
            return
        name = table.cursor_place()
        if name is None:
            return
        self.app.copy_to_clipboard(name)
        self.log_line(f"copied: {name}")

    def action_smart_escape(self) -> None:
        # A single Escape clears everything it can in one press (filter,
        # marks) rather than requiring one press per concern. Help is a
        # modal now, so it handles its own escape.
        filter_bar = self.query_one(FilterBar)
        changed = False
        if self.focused is filter_bar or filter_bar.value:
            changed = self._close_filter()
        table = self.query_one(DeviceTable)
        changed = self._clear_marks(table, refresh=False) or changed
        if changed:
            self.refresh_fleet()
        if self.focused is not table:
            table.focus()

    def _clear_marks(self, table: DeviceTable, *, refresh: bool = True) -> bool:
        if not table.marks:
            return False
        table.marks.clear()
        if refresh:
            self.refresh_fleet()
        return True

    def _close_filter(self) -> bool:
        """Hide and clear the filter; return whether the table's query changed.

        The caller refreshes; a re-render is not left to layout side effects
        of hiding the bar.
        """
        filter_bar = self.query_one(FilterBar)
        filter_bar.value = ""
        filter_bar.add_class("hidden")
        table = self.query_one(DeviceTable)
        changed = bool(table.filter_query)
        # Input.value's Changed message (and so FilterBar's FilterChanged)
        # is posted asynchronously, reaching on_filter_bar_filter_changed
        # only on a later message-pump cycle. action_smart_escape calls
        # _clear_marks right after this, which refreshes synchronously; set
        # the table's query here too so that refresh already reflects the
        # cleared filter instead of one more render with a stale query.
        table.filter_query = ""
        table.focus()
        return changed

    def on_input_submitted(self, message: Input.Submitted) -> None:
        # Enter in the filter bar is a keyboard path out of it, mirroring
        # the escape path (action_smart_escape) below.
        if message.input is self.query_one(FilterBar) and self._close_filter():
            self.refresh_fleet()

    def on_filter_bar_filter_changed(self, message: FilterBar.FilterChanged) -> None:
        table = self.query_one(DeviceTable)
        if table.filter_query == message.query:
            # Already applied synchronously (see _close_filter); this is
            # the deferred FilterChanged for that same change catching up,
            # not a new one: skip the redundant refresh.
            return
        table.filter_query = message.query
        self.refresh_fleet()

    def on_device_table_marks_changed(self, _message: DeviceTable.MarksChanged) -> None:
        self._refresh_status()
        if self._tour_hook is not None:
            self._tour_hook("marks_changed", "")

    def on_device_table_cursor_changed(self, message: DeviceTable.CursorChanged) -> None:
        if self._tour_hook is not None:
            self._tour_hook("cursor_changed", message.place_name or "")

    def _refresh_status(self) -> None:
        # Same window as the DeviceTable guard in refresh_fleet: a message
        # can arrive while the screen is being pushed or torn down.
        try:
            self.query_one(StatusBar).refresh_status()
        except NoMatches:
            return

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable consumes enter before screen bindings fire (it posts
        # RowSelected instead); handle the fleet table's enter-with-focus
        # path here.
        if event.data_table is self.query_one(DeviceTable):
            self.action_toggle_detail()

    # ------------------------------------------------------------------
    # Coordinators: switch/create/edit/delete via the CoordinatorSelector
    # ------------------------------------------------------------------

    def _coordinator_field_specs(self) -> list[FieldSpec]:
        return [
            FieldSpec("name", "Name", "lab", validate_name),
            FieldSpec("address", "Address", "host:port", validate_coordinator),
            FieldSpec("prefix", "Prefix (optional)", "labgrid-client -x host:port"),
        ]

    def action_switch_coordinator(self) -> None:
        coordinators = getattr(self.app, "coordinators", None)
        if coordinators is None:
            return
        entries = sorted(coordinators.entries.values(), key=lambda e: e.name)
        self.app.push_screen(
            CoordinatorSelector(entries, coordinators.current),
            callback=self._on_coordinator_selector_result,
        )

    def _on_coordinator_selector_result(self, result: CoordinatorSelectorResult) -> None:
        coordinators = getattr(self.app, "coordinators", None)
        if coordinators is None or result is None:
            return
        if isinstance(result, SwitchResult):
            if result.name != coordinators.current:
                switch = getattr(self.app, "switch_coordinator", None)
                if switch is not None:
                    switch(result.name)
        elif isinstance(result, CreateResult):
            self._show_coordinator_edit(None)
        elif isinstance(result, EditResult):
            self._show_coordinator_edit(result.name)
        elif isinstance(result, DeleteResult):
            self.app.push_screen(
                CoordinatorDeleteConfirm(result.name),
                callback=lambda confirmed: self._on_coordinator_delete_confirmed(
                    result.name, confirmed
                ),
            )

    def _show_coordinator_edit(self, name: str | None) -> None:
        coordinators = getattr(self.app, "coordinators", None)
        if coordinators is None:
            return
        entry = coordinators.entries.get(name) if name else None
        initial = {
            "name": entry.name if entry else "",
            "address": entry.address if entry else "",
            "prefix": (entry.prefix or "") if entry else "",
        }
        title = f"Edit Coordinator: {name}" if name else "New Coordinator"
        locked = frozenset({"name"}) if name else frozenset()
        self.app.push_screen(
            CoordinatorEditModal(title, self._coordinator_field_specs(), initial, locked),
            callback=lambda values: self._on_coordinator_edit_result(name, values),
        )

    def _persist_coordinators(self) -> bool:
        return cast("LabgridTuiApp", self.app).persist_coordinators

    def _on_coordinator_edit_result(
        self, editing: str | None, values: dict[str, str] | None
    ) -> None:
        if values is None:
            return
        coordinators = getattr(self.app, "coordinators", None)
        coordinators_path = getattr(self.app, "coordinators_path", None)
        if coordinators is None or coordinators_path is None:
            return
        try:
            if editing is None:
                name = validate_name(values["name"])
                if name in coordinators.entries:
                    self.notify(f"coordinator {name!r} already exists", severity="error")
                    return
            else:
                name = editing
            address = validate_coordinator(values["address"])
        except CoordinatorError as exc:
            self.notify(str(exc), severity="error")
            return
        prefix = values["prefix"].strip() or None
        existing = coordinators.entries.get(name)
        extra = existing.extra if existing is not None else {}
        coordinators.entries[name] = CoordinatorEntry(
            name=name, address=address, prefix=prefix, extra=extra
        )
        if self._persist_coordinators():
            save_coordinators(coordinators_path, coordinators)
        self.log_line(f"coordinator {'updated' if editing else 'created'}: {name}")
        if editing is not None and editing == coordinators.current:
            # The active coordinator's own address/prefix may have just
            # changed; reconnect so the running session picks it up
            # instead of the TUI staying on the pre-edit address.
            switch = getattr(self.app, "switch_coordinator", None)
            if switch is not None:
                switch(name)

    def _on_coordinator_delete_confirmed(self, name: str, confirmed: bool) -> None:
        if not confirmed:
            return
        coordinators = getattr(self.app, "coordinators", None)
        coordinators_path = getattr(self.app, "coordinators_path", None)
        if coordinators is None or coordinators_path is None:
            return
        if name == coordinators.current:
            self.notify("cannot delete the active coordinator", severity="warning")
            return
        coordinators.entries.pop(name, None)
        if self._persist_coordinators():
            save_coordinators(coordinators_path, coordinators)
        self.log_line(f"coordinator deleted: {name}")
