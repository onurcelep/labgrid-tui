"""Fleet table: sorted rows, identity-preserving cursor, marks, filter.

Rendering uses a consistent dashboard vocabulary: colored status dots,
capability abbreviation chips, humanized change ages, and dynamic columns
for the fleet's most common tag keys.
"""

import time

from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.geometry import Region
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import RowDoesNotExist

from labgrid_tui.coordinator.models import Place, Resource
from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.model.capabilities import capabilities_for, capability_of
from labgrid_tui.ui.format import capability_chips, format_age, top_tag_keys
from labgrid_tui.ui.layout import is_narrow, middle_ellipsis
from labgrid_tui.ui.store import FleetStore
from labgrid_tui.ui.widgets.filter_bar import matches_filter

MARK = "●"

# Status dots: acquired -> blue, reserved -> black, nothing usable exported
# (no resource matched, or every matched one offline) -> red, free -> green.
DOT_ACQUIRED = "\U0001f535"
DOT_RESERVED = "⚫"
DOT_OFFLINE = "\U0001f534"
DOT_FREE = "\U0001f7e2"

# Cap on the Name column's rendered width in -narrow, so one long place
# name can't push the always-shown columns (M/Name/S/Capabilities) off
# the right edge before the column-priority drop even gets a chance.
NAME_MAX_WIDTH_NARROW = 20

# DataTable's own per-column padding (Column.get_render_width: content
# width + 2 * cell_padding, cell_padding defaults to 1): folded into the
# column-priority width estimate so it matches what DataTable will
# actually render closely enough to avoid a scrollbar for no reason.
_CELL_PADDING = 2

# Fixed leading/trailing column keys; dynamic tag columns are inserted
# between "user" and "changed". Order here is left-to-right display order,
# not drop priority: see _DROP_ORDER below.
_LEAD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("M", "m"),
    ("Name", "name"),
    ("S", "s"),
    ("Capabilities", "capabilities"),
    ("User", "user"),
)
_TRAIL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Changed", "changed"),
    ("Comment", "comment"),
)

# Columns that are always shown regardless of width: identity, status, and
# what a place can do. Everything else is a candidate for dropping.
_PROTECTED_KEYS = frozenset({"m", "name", "s", "capabilities"})


def _cell_fingerprint(cell: str | Text) -> str:
    """Hashable representation of a cell for diff comparison.

    Rich ``Text`` isn't directly comparable with ``==`` in a way that
    reflects styling, so this captures the plain text plus the base
    style and per-span styles: a dimmed cell or a capability chip
    flipping from green to red must register as a change even when the
    plain text is identical.
    """
    if isinstance(cell, Text):
        base = str(cell.style) if cell.style else ""
        spans = ",".join(f"{s.start}-{s.end}:{s.style}" for s in cell._spans)
        return f"{cell.plain}|{base}|{spans}"
    return cell


class DeviceTable(DataTable[str | Text]):
    DEFAULT_CSS = """
    DeviceTable {
        height: 1fr;
    }
    /* Translucent accent tint for the cursor row. Foreground colors are
       preserved via cursor_foreground_priority="renderable" so per-cell
       Rich Text styles (green/red capability chips) stay visible on the
       selected row. */
    DeviceTable > .datatable--cursor {
        background: $accent 30%;
    }
    DeviceTable:focus > .datatable--cursor {
        background: $accent 50%;
    }
    """

    BINDINGS = [
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("space", "toggle_mark", "Mark", key_display="Space"),
        Binding("ctrl+a", "mark_all", "Mark all", show=False),
        # Vim/fzf-style paging and horizontal scroll. scroll_top/scroll_bottom
        # and page_up/page_down are DataTable's own actions (already bound to
        # ctrl+home/ctrl+end and pageup/pagedown respectively); these are
        # aliases. scroll_left/scroll_right come from the base ScrollView and
        # never touch cursor_coordinate, so h/l pan a wide table without
        # moving the row cursor.
        Binding("g", "scroll_top", "Top", show=False),
        Binding("G", "scroll_bottom", "Bottom", show=False),
        Binding("ctrl+d", "half_page_down", "Half page down", show=False),
        Binding("ctrl+u", "half_page_up", "Half page up", show=False),
        Binding("ctrl+f", "page_down", "Page down", show=False),
        Binding("ctrl+b", "page_up", "Page up", show=False),
        Binding("h", "scroll_left", "Scroll left", show=False),
        Binding("l", "scroll_right", "Scroll right", show=False),
    ]

    class CursorChanged(Message):
        def __init__(self, place_name: str | None) -> None:
            super().__init__()
            self.place_name = place_name

    class MarksChanged(Message):
        """Posted whenever a table-bound key (space/ctrl+a) changes marks.

        Lets the status bar's "marked: N" segment stay live without the
        table needing to know about its siblings; callers that mutate
        ``marks`` directly (bulk dispatch, escape) already trigger a full
        status refresh themselves.
        """

    def __init__(self) -> None:
        super().__init__(id="fleet-table", cursor_type="row")
        self.cursor_foreground_priority = "renderable"
        self.zebra_stripes = True
        self.marks: set[str] = set()
        self.filter_query: str = ""
        self._cache: tuple[FleetStore, dict[str, str] | None] | None = None
        # Last cursor place seen while the store held real (LIVE) data;
        # survives a reconnect's empty-store refresh so the cursor can be
        # restored once the fleet repopulates.
        self._last_cursor: str | None = None
        # Tag keys currently rendered as columns (subset of _all_tag_keys
        # once width forces some of them to drop) and the full candidate
        # set from the fleet's most common tag keys, independent of width.
        self._tag_columns: list[str] = []
        self._all_tag_keys: list[str] = []
        self._column_keys: tuple[str, ...] = ()
        # Diff cache for cell-level updates: place name -> per-column
        # fingerprints, in the same order as _column_keys. Ordered list of
        # visible place names from the last render, to detect when the
        # visible set itself changed (add/remove/filter) and a full
        # rebuild is required.
        self._prev_fingerprints: dict[str, tuple[str, ...]] = {}
        self._prev_visible_names: list[str] = []

    def on_mount(self) -> None:
        # No fleet data yet, so no tag columns; show every fixed column
        # until the first refresh_rows() call has real content to size.
        self._build_columns(tuple(key for _label, key in _LEAD_COLUMNS + _TRAIL_COLUMNS), [])

    def on_resize(self, _event: events.Resize) -> None:
        # Column priority depends on the table's own width, not just on
        # fleet data changing; a resize alone must be able to add back a
        # column that only just regained enough room.
        self._rerender()

    def _build_columns(self, column_keys: tuple[str, ...], tag_keys: list[str]) -> None:
        self.clear(columns=True)
        all_columns = (
            _LEAD_COLUMNS
            + tuple((key.capitalize(), f"tag_{key}") for key in tag_keys)
            + _TRAIL_COLUMNS
        )
        columns = tuple((label, key) for label, key in all_columns if key in column_keys)
        self.add_columns(*columns)
        self._all_tag_keys = tag_keys
        self._tag_columns = [key for key in tag_keys if f"tag_{key}" in column_keys]
        self._column_keys = tuple(key for _label, key in columns)
        # A column rebuild clears every row; cached fingerprints and the
        # visible-name list are now meaningless.
        self._prev_fingerprints.clear()
        self._prev_visible_names = []

    @staticmethod
    def _drop_order(tag_keys: list[str]) -> list[str]:
        """Column keys in width-pressure drop order, least valuable first.

        Tags drop from the least common upward; user is the last of the
        droppable columns to go, since who holds a place is worth more
        than a tag/timestamp/comment column once something has to give.
        """
        return ["comment", "changed", *[f"tag_{key}" for key in reversed(tag_keys)], "user"]

    def _visible_columns(
        self,
        tag_keys: list[str],
        rows: list[dict[str, str | Text]],
        available_width: int,
    ) -> tuple[str, ...]:
        all_columns = (
            _LEAD_COLUMNS
            + tuple((key.capitalize(), f"tag_{key}") for key in tag_keys)
            + _TRAIL_COLUMNS
        )
        content_width = {key: len(label) for label, key in all_columns}
        for row in rows:
            for key, cell in row.items():
                text = cell.plain if isinstance(cell, Text) else cell
                if len(text) > content_width[key]:
                    content_width[key] = len(text)

        shown = {key for _label, key in all_columns}

        def total_width() -> int:
            return sum(content_width[key] + _CELL_PADDING for key in shown)

        if available_width > 0:
            for key in self._drop_order(tag_keys):
                if key not in shown or key in _PROTECTED_KEYS:
                    continue
                if total_width() <= available_width:
                    break
                shown.discard(key)
        return tuple(key for _label, key in all_columns if key in shown)

    def cursor_place(self) -> str | None:
        if not self.row_count:
            return None
        cell_key = self.coordinate_to_cell_key(self.cursor_coordinate)
        value = cell_key.row_key.value
        return str(value) if value is not None else None

    def selected_targets(self) -> list[str]:
        if self.marks:
            return sorted(self.marks)
        place = self.cursor_place()
        return [place] if place else []

    def refresh_rows(self, store: FleetStore, capability_extra: dict[str, str] | None) -> None:
        self._cache = (store, capability_extra)
        # cursor_place() reads the table's still-live rows from before this
        # call touches them, so it reflects any cursor move (e.g. j/k) made
        # since the last refresh; fall back to the last known place once a
        # prior stale refresh has already emptied the table.
        current_cursor = self.cursor_place()
        if current_cursor is not None:
            self._last_cursor = current_cursor
        selected = current_cursor if current_cursor is not None else self._last_cursor
        # A reconnect's replay clears the store to an empty transient state
        # before the fleet repopulates; pruning marks against that empty
        # snapshot would discard them for no reason other than a connection
        # blip.
        stale = store.conn is not ConnState.LIVE or not store.places
        now = time.time()
        # (place, resources) pairs: resources_of is O(places x resources)
        # per call, so it is computed once here per refresh and threaded
        # through instead of every consumer below re-deriving it.
        visible = self._visible_places(store, capability_extra)

        if not stale:
            self.marks &= set(store.places)
            tag_keys = top_tag_keys(store.places.values())
            rows = [
                self._cell_values(place, resources, capability_extra, now, tag_keys)
                for place, resources in visible
            ]
            column_keys = self._visible_columns(tag_keys, rows, self._available_width())
            if column_keys != self._column_keys or tag_keys != self._all_tag_keys:
                self._build_columns(column_keys, tag_keys)

        new_names = [place.name for place, _resources in visible]

        if new_names != self._prev_visible_names:
            self._rebuild_rows(visible, capability_extra, now, selected)
        else:
            self._diff_update_rows(visible, capability_extra, now)

        if self.row_count:
            self._last_cursor = self.cursor_place()

    def _available_width(self) -> int:
        # A 1-cell safety margin covers a vertical scrollbar appearing
        # once rows overflow the table's height, which would otherwise
        # shrink the actual content width by one column after the fact.
        return max(0, self.size.width - 1)

    def _visible_places(
        self, store: FleetStore, capability_extra: dict[str, str] | None
    ) -> list[tuple[Place, list[Resource]]]:
        visible = []
        for place in sorted(store.places.values(), key=lambda p: p.name):
            resources = store.resources_of(place)
            caps = capabilities_for(resources, capability_extra)
            if matches_filter(place, caps, self.filter_query):
                visible.append((place, resources))
        return visible

    def _rebuild_rows(
        self,
        visible: list[tuple[Place, list[Resource]]],
        capability_extra: dict[str, str] | None,
        now: float,
        selected: str | None,
    ) -> None:
        """Clear and re-populate rows: the visible set changed structurally."""
        self.clear()
        self._prev_fingerprints.clear()
        self._prev_visible_names = [place.name for place, _resources in visible]
        for place, resources in visible:
            cells = self._row_cells(place, resources, capability_extra, now)
            self.add_row(*cells, key=place.name)
            self._prev_fingerprints[place.name] = tuple(_cell_fingerprint(c) for c in cells)
        if selected is not None and self.row_count:
            try:
                self.move_cursor(row=self.get_row_index(selected))
            except RowDoesNotExist:
                # Selected place was removed or filtered out; fall back to
                # the top row rather than leaving the cursor on stale index.
                self.move_cursor(row=0)

    def _diff_update_rows(
        self,
        visible: list[tuple[Place, list[Resource]]],
        capability_extra: dict[str, str] | None,
        now: float,
    ) -> None:
        """Update only changed cells: the visible set is unchanged, so the
        cursor and scroll position are left untouched."""
        for place, resources in visible:
            cells = self._row_cells(place, resources, capability_extra, now)
            fingerprints = tuple(_cell_fingerprint(c) for c in cells)
            prev = self._prev_fingerprints.get(place.name)
            if prev == fingerprints:
                continue
            for col_idx, (col_key, cell) in enumerate(zip(self._column_keys, cells, strict=True)):
                if prev is None or col_idx >= len(prev) or prev[col_idx] != fingerprints[col_idx]:
                    self.update_cell(place.name, col_key, cell, update_width=True)
            self._prev_fingerprints[place.name] = fingerprints

    def _row_cells(
        self,
        place: Place,
        resources: list[Resource],
        capability_extra: dict[str, str] | None,
        now: float,
    ) -> list[str | Text]:
        values = self._cell_values(place, resources, capability_extra, now, self._tag_columns)
        return [values[key] for key in self._column_keys]

    def _cell_values(
        self,
        place: Place,
        resources: list[Resource],
        capability_extra: dict[str, str] | None,
        now: float,
        tag_keys: list[str],
    ) -> dict[str, str | Text]:
        """Every candidate cell for ``place``, keyed by column key.

        Computed for the full candidate column set (not just the ones
        currently built) so the column-priority width estimate in
        ``_visible_columns`` can measure a column before deciding whether
        to show it.
        """
        online: set[str] = set()
        offline: set[str] = set()
        unknown = False
        for resource in resources:
            capability = capability_of(resource, capability_extra)
            if capability is None:
                unknown = True
            elif resource.avail:
                online.add(capability)
            else:
                offline.add(capability)
        offline -= online

        all_offline = not any(r.avail for r in resources)
        if place.acquired:
            dot = DOT_ACQUIRED
        elif place.reservation:
            dot = DOT_RESERVED
        elif all_offline:
            dot = DOT_OFFLINE
        else:
            dot = DOT_FREE

        user = place.acquired.split("/")[-1] if place.acquired else "-"
        changed = format_age(now - place.changed) if place.changed else "-"

        def dimmed(value: str) -> str | Text:
            # An all-offline place is not currently operable; dim its plain
            # cells (chips already carry red).
            return Text(value, style="dim") if all_offline else value

        values: dict[str, str | Text] = {
            "m": self._mark_cell(place.name),
            "name": dimmed(self._display_name(place.name)),
            "s": dot,
            "capabilities": capability_chips(online, offline, unknown=unknown),
            "user": dimmed(user),
            "changed": dimmed(changed),
            "comment": dimmed(place.comment),
        }
        for key in tag_keys:
            values[f"tag_{key}"] = dimmed(place.tags.get(key, "-"))
        return values

    def _mark_cell(self, name: str) -> str:
        return MARK if name in self.marks else ""

    def cursor_row_region(self) -> Region | None:
        """Screen region of the row under the cursor, clipped to what is visible.

        Stops at the end of the Name column rather than spanning the table:
        the tour anchors its step card to this region, and a bench's name is
        the part of its row that identifies it.

        DataTable exposes row geometry only in its own virtual coordinates
        (``_get_row_region``/``_get_cell_region``, verified against the
        installed Textual), so the header height and both scroll offsets are
        applied here to land in screen coordinates.
        """
        row_index = self.cursor_row
        if not self.is_valid_row_index(row_index):
            return None
        content = self.scrollable_content_region
        header = self.header_height if self.show_header else 0
        body = Region(content.x, content.y + header, content.width, content.height - header)
        if not body.area:
            return None
        row = self._get_row_region(row_index)
        width = row.width
        try:
            name_column = self._column_keys.index("name")
        except ValueError:
            pass  # no Name column yet: anchor on the whole row
        else:
            width = self._get_cell_region(Coordinate(row_index, name_column)).right
        placed = Region(
            body.x - self.scroll_offset.x,
            content.y + row.y - self.scroll_offset.y,
            width,
            row.height,
        )
        visible = placed.intersection(body)
        return visible if visible.area else None

    def _display_name(self, name: str) -> str:
        # Capped only in -narrow: place names commonly share a long
        # prefix, so the tail, where they diverge, is what must
        # survive truncation, not the head.
        if is_narrow(self.app.size):
            return middle_ellipsis(name, NAME_MAX_WIDTH_NARROW)
        return name

    def _rerender(self) -> None:
        if self._cache is not None:
            self.refresh_rows(*self._cache)

    def action_toggle_mark(self) -> None:
        place = self.cursor_place()
        if place is None:
            return
        if place in self.marks:
            self.marks.discard(place)
        else:
            self.marks.add(place)
        self._rerender()
        self.post_message(self.MarksChanged())

    def action_half_page_down(self) -> None:
        self._move_cursor_relative(self._half_page_rows())

    def action_half_page_up(self) -> None:
        self._move_cursor_relative(-self._half_page_rows())

    def _half_page_rows(self) -> int:
        header = self.header_height if self.show_header else 0
        return max(1, (self.size.height - header) // 2)

    def _move_cursor_relative(self, delta: int) -> None:
        if not self.show_cursor or self.cursor_type not in ("cell", "row"):
            return
        row, column = self.cursor_coordinate
        target = max(0, min(self.row_count - 1, row + delta))
        self.move_cursor(row=target, column=column)

    def action_mark_all(self) -> None:
        """Mark all visible rows, or unmark all if every visible row is
        already marked."""
        visible = {
            str(value)
            for row in range(self.row_count)
            if (value := self.coordinate_to_cell_key(Coordinate(row, 0)).row_key.value) is not None
        }
        if visible and visible <= self.marks:
            self.marks.clear()
        else:
            self.marks |= visible
        self._rerender()
        self.post_message(self.MarksChanged())

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        value = event.row_key.value if event.row_key is not None else None
        name = str(value) if value else None
        self.post_message(self.CursorChanged(name))
