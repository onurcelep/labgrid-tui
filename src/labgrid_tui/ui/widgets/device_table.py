"""Fleet table: sorted rows, identity-preserving cursor, marks, filter.

Rendering uses a consistent dashboard vocabulary: colored status dots,
one chip per matched resource class, humanized change ages, and the
place's tags on one line, in fleet-wide slots so the same key sits at the
same column on every row.
"""

import time

from rich.cells import cell_len
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
from labgrid_tui.ui.format import (
    TagLayout,
    capability_chips,
    fit_layout,
    format_age,
    format_tags,
    tag_layout,
)
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
# name can't push the always-shown columns (M/Name/S/Resources) off
# the right edge before the column-priority drop even gets a chance.
NAME_MAX_WIDTH_NARROW = 20

# DataTable's own per-column padding (Column.get_render_width: content
# width + 2 * cell_padding, cell_padding defaults to 1): folded into the
# column-priority width estimate so it matches what DataTable will
# actually render closely enough to avoid a scrollbar for no reason.
_CELL_PADDING = 2

# Leading/trailing column keys. Order here is left-to-right display order,
# not drop priority: see _DROP_ORDER below.
_LEAD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("M", "m"),
    ("Name", "name"),
    ("S", "s"),
    # "Resources" is labgrid's own word for what a place matches; the
    # internal key stays "capabilities", the name of the chip mapping.
    ("Resources", "capabilities"),
    ("User", "user"),
)
_TRAIL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("Tags", "tags"),
    ("Changed", "changed"),
    ("Comment", "comment"),
)
_ALL_COLUMNS: tuple[tuple[str, str], ...] = _LEAD_COLUMNS + _TRAIL_COLUMNS

# Column keys in width-pressure drop order, least valuable first. Whatever
# is not listed here is always shown: identity, status, and what a place
# can do. Comment goes first because most places carry none; user is the
# last to go, since who holds a place outranks a timestamp.
_DROP_ORDER: tuple[str, ...] = ("comment", "tags", "changed", "user")

# Floor the Tags column shrinks to before it is considered for dropping.
# Tags shrink ahead of every drop, so the default 80-column terminal keeps
# a cut Tags column rather than losing it; below roughly one key=value
# pair the column would cost more width than it informs.
TAGS_MIN_WIDTH = 12


def _cell_fingerprint(cell: str | Text) -> str:
    """Hashable representation of a cell for diff comparison.

    Rich ``Text`` isn't directly comparable with ``==`` in a way that
    reflects styling, so this captures the plain text plus the base
    style and per-span styles: a dimmed cell or a resource chip
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
       Rich Text styles (green/red resource chips) stay visible on the
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
        # Width the Tags cells are cut to, or None when they render in
        # full; set by the column-priority pass, which is what knows how
        # much room is left once every other column has its own.
        self._tags_width: int | None = None
        # Fleet-wide tag slots, and the subset of them that survives
        # _tags_width. Both are per-fleet, never per-row: rows only line up
        # if every one of them renders the same slots at the same widths.
        self._tag_layout = TagLayout()
        self._render_layout = TagLayout()
        # Whether the Name cells carry their place's aliases, and the width
        # those cells need without them. Both are settled by the same
        # column-priority pass, for the same reason the Tags cut is.
        self._show_aliases = True
        self._name_width_bare = 0
        # Whether any place in the fleet carries a comment at all; the
        # Comment column is not built when none does.
        self._any_comment = False
        self._column_keys: tuple[str, ...] = ()
        # Diff cache for cell-level updates: place name -> per-column
        # fingerprints, in the same order as _column_keys. Ordered list of
        # visible place names from the last render, to detect when the
        # visible set itself changed (add/remove/filter) and a full
        # rebuild is required.
        self._prev_fingerprints: dict[str, tuple[str, ...]] = {}
        self._prev_visible_names: list[str] = []

    def on_mount(self) -> None:
        # No fleet data yet to size columns against; show all of them
        # until the first refresh_rows() call has real content.
        self._build_columns(tuple(key for _label, key in _ALL_COLUMNS))

    def on_resize(self, _event: events.Resize) -> None:
        # Column priority depends on the table's own width, not just on
        # fleet data changing; a resize alone must be able to add back a
        # column that only just regained enough room.
        self._rerender()

    def _build_columns(self, column_keys: tuple[str, ...]) -> None:
        self.clear(columns=True)
        columns = tuple((label, key) for label, key in _ALL_COLUMNS if key in column_keys)
        self.add_columns(*columns)
        self._column_keys = tuple(key for _label, key in columns)
        # A column rebuild clears every row; cached fingerprints and the
        # visible-name list are now meaningless.
        self._prev_fingerprints.clear()
        self._prev_visible_names = []

    def _visible_columns(
        self,
        rows: list[dict[str, str | Text]],
        available_width: int,
    ) -> tuple[str, ...]:
        """Which columns fit, whether the names keep their aliases, and how
        wide the Tags cells may render.

        Sets ``_show_aliases``, ``_tags_width`` and ``_render_layout`` as
        side effects. Under pressure the aliases go first: they are a second
        name for a place the row already names, so they cost less than a tag
        pair or a whole column. Tags then give up width before anything
        gives up its place, so a heavily tagged fleet costs its own cells a
        cut, not the table a column.

        Cells are measured in terminal cells, not characters: a status dot
        is one character and two cells wide, and estimating it at one would
        leave the table overflowing its own width by a column per emoji.
        """
        content_width = {key: len(label) for label, key in _ALL_COLUMNS}
        for row in rows:
            for key, cell in row.items():
                text = cell.plain if isinstance(cell, Text) else cell
                if cell_len(text) > content_width[key]:
                    content_width[key] = cell_len(text)

        # Tags measures against the layout, not against the widest row:
        # every row reserves every slot, so a row whose last key is missing
        # still holds that slot's width open.
        content_width["tags"] = max(content_width["tags"], self._tag_layout.total_width)

        shown = {key for _label, key in _ALL_COLUMNS}
        if not self._any_comment:
            # A labelled strip of blanks says nothing about the fleet, and
            # labgrid-client's own places listing prints the comment only
            # where there is one. Measured over the whole fleet, not the
            # filtered rows, so typing in the filter cannot make the column
            # appear and disappear under the reader.
            shown.discard("comment")

        def total_width() -> int:
            return sum(content_width[key] + _CELL_PADDING for key in shown)

        tags_width: int | None = None
        show_aliases = True
        natural_tags = content_width["tags"]
        if available_width > 0:
            # The rows were rendered with the aliases on, so this is the
            # pass that finds out whether they fit at all.
            if total_width() > available_width:
                show_aliases = False
                content_width["name"] = max(len("Name"), self._name_width_bare)
            # Shrink Tags next, so a wide tag set costs its own cells a
            # cut rather than the table a whole column...
            excess = total_width() - available_width
            if excess > 0:
                content_width["tags"] = max(TAGS_MIN_WIDTH, natural_tags - excess)
            for key in _DROP_ORDER:
                if total_width() <= available_width:
                    break
                shown.discard(key)
            # ...then hand back whatever the drops freed, so Tags is only
            # as cut as the columns that survived actually require.
            slack = available_width - total_width()
            if slack > 0:
                content_width["tags"] = min(natural_tags, content_width["tags"] + slack)
            if content_width["tags"] < natural_tags:
                tags_width = content_width["tags"]
        self._show_aliases = show_aliases
        self._set_tag_render(tags_width)
        return tuple(key for _label, key in _ALL_COLUMNS if key in shown)

    def _set_tag_render(self, width: int | None) -> None:
        """Fix how every Tags cell renders until the next width decision.

        Which slots a cut keeps is a property of the layout and the width,
        never of one place's tags: settling it here, once, is what keeps the
        surviving pairs in the same columns on every row.
        """
        self._tags_width = width
        self._render_layout = (
            self._tag_layout if width is None else fit_layout(self._tag_layout, width)
        )

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
        # Over the whole fleet, not the filtered rows: a filter must not be
        # able to shift the tag columns or turn a key constant. A changed
        # layout re-renders every Tags cell, so the diff pass picks it up
        # like any other cell change and re-measures the column with it.
        self._tag_layout = tag_layout(place.tags for place in store.places.values())
        self._set_tag_render(self._tags_width)
        self._any_comment = any(place.comment for place in store.places.values())
        # (place, resources) pairs: resources_of is O(places x resources)
        # per call, so it is computed once here per refresh and threaded
        # through instead of every consumer below re-deriving it.
        visible = self._visible_places(store, capability_extra)

        if not stale:
            self.marks &= set(store.places)
            # Measure the Name column at its widest, aliases included, and
            # record what it would need without them; _visible_columns
            # decides between the two.
            self._show_aliases = True
            self._name_width_bare = max(
                (len(self._name_plain(place, aliases=False)) for place, _r in visible),
                default=0,
            )
            rows = [
                self._cell_values(place, resources, capability_extra, now)
                for place, resources in visible
            ]
            column_keys = self._visible_columns(rows, self._available_width())
            if column_keys != self._column_keys:
                self._build_columns(column_keys)

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
        values = self._cell_values(place, resources, capability_extra, now)
        return [values[key] for key in self._column_keys]

    def _cell_values(
        self,
        place: Place,
        resources: list[Resource],
        capability_extra: dict[str, str] | None,
        now: float,
    ) -> dict[str, str | Text]:
        """Every candidate cell for ``place``, keyed by column key.

        Computed for the full column set, not just the ones currently
        built, so the column-priority width estimate in ``_visible_columns``
        can measure a column before deciding whether to show it, shrink it,
        or drop it. Every column but Tags renders in full here; Tags renders
        at the width that pass last settled on, and is measured against the
        layout instead.
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

        def dimmed(value: str | Text) -> str | Text:
            # An all-offline place is not currently operable; dim its plain
            # cells (chips already carry red).
            if not all_offline:
                return value
            if isinstance(value, Text):
                value.style = "dim"
                return value
            return Text(value, style="dim")

        values: dict[str, str | Text] = {
            "m": self._mark_cell(place.name),
            "name": dimmed(self._display_name(place)),
            "s": dot,
            "capabilities": capability_chips(online, offline, unknown=unknown),
            "user": dimmed(user),
            "tags": dimmed(format_tags(place.tags, self._render_layout, width=self._tags_width)),
            "changed": dimmed(changed),
            "comment": dimmed(place.comment),
        }
        return values

    def _mark_cell(self, name: str) -> str:
        return MARK if name in self.marks else ""

    def row_region(self, row_index: int) -> Region | None:
        """Screen region of one rendered row, clipped to what is visible.

        Stops at the end of the last column rather than spanning the widget:
        DataTable's own row geometry is as wide as the widget, so on a table
        with spare width it would report blank cells as part of the row.

        DataTable exposes row geometry only in its own virtual coordinates
        (``_get_row_region``/``_get_cell_region``, verified against the
        installed Textual), so the header height and both scroll offsets are
        applied here to land in screen coordinates.
        """
        if not self.is_valid_row_index(row_index):
            return None
        content = self.scrollable_content_region
        header = self.header_height if self.show_header else 0
        body = Region(content.x, content.y + header, content.width, content.height - header)
        if not body.area:
            return None
        row = self._get_row_region(row_index)
        placed = Region(
            content.x - self.scroll_offset.x,
            content.y + row.y - self.scroll_offset.y,
            self._rendered_width(row_index) or row.width,
            row.height,
        )
        visible = placed.intersection(body)
        return visible if visible.area else None

    def _rendered_width(self, row_index: int) -> int:
        """Right edge of the last column, in the table's virtual coordinates."""
        last_column = len(self.columns) - 1
        if last_column < 0:
            return 0
        return int(self._get_cell_region(Coordinate(row_index, last_column)).right)

    def rows_block_region(self) -> Region | None:
        """Screen region of the rendered rows: header through the last bench.

        The tour anchors its step card to the block rather than to the row
        under the cursor. A card placed below one row would sit on the
        benches underneath it, and the table exists to show them; below the
        block it lands in the table's own free space instead. The cursor
        row highlight is what still singles out the bench a step is about.
        """
        content = self.scrollable_content_region
        if not self.row_count or not content.area:
            return None
        last = self.row_region(self.row_count - 1)
        if last is None:
            # The last bench is scrolled out of view, so the block is
            # everything the viewport currently shows.
            bottom, width = content.bottom, self._rendered_width(0)
        else:
            bottom, width = last.bottom, last.width
        block = Region(content.x - self.scroll_offset.x, content.y, width, bottom - content.y)
        visible = block.intersection(content)
        return visible if visible.area else None

    def _name_plain(self, place: Place, *, aliases: bool) -> str:
        """The Name cell's text: ``name (alias alias)``, as
        ``labgrid-client places`` prints it.

        The aliases are the only part of this cell width pressure may take:
        the name is what sorts the fleet, keys the row and goes into every
        command line, so both budgets here (the -narrow cap and the
        column-priority pass) drop the aliases whole before the name is
        elided at all.
        """
        name = place.name
        suffix = f" ({' '.join(place.aliases)})" if aliases and place.aliases else ""
        if is_narrow(self.app.size) and len(name) + len(suffix) > NAME_MAX_WIDTH_NARROW:
            # Place names commonly share a long prefix, so the tail, where
            # they diverge, is what must survive truncation.
            return middle_ellipsis(name, NAME_MAX_WIDTH_NARROW)
        return name + suffix

    def _display_name(self, place: Place) -> str | Text:
        plain = self._name_plain(place, aliases=self._show_aliases)
        if plain == place.name:
            return plain
        suffix = plain.removeprefix(place.name)
        if not suffix:
            return plain  # the name itself was elided; nothing to dim
        text = Text(place.name)
        text.append(suffix, style="dim")
        return text

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
