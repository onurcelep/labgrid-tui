from textual.app import App, ComposeResult

from labgrid_tui.coordinator.models import Place, Resource, ResourceMatchPattern
from labgrid_tui.coordinator.stream import ConnectionChanged, ConnState
from labgrid_tui.ui.format import tag_layout
from labgrid_tui.ui.store import FleetStore
from labgrid_tui.ui.widgets.device_table import DeviceTable


def _place(
    name: str,
    comment: str = "",
    acquired: str | None = None,
    tags: dict[str, str] | None = None,
    matches: tuple[ResourceMatchPattern, ...] = (),
    aliases: tuple[str, ...] = (),
) -> Place:
    return Place(
        name=name,
        aliases=aliases,
        comment=comment,
        tags=tags or {},
        matches=matches,
        acquired=acquired,
        acquired_resources=(),
        allowed=(),
        created=0.0,
        changed=0.0,
        reservation=None,
    )


def _use_layout(table: DeviceTable, places: list[Place], width: int | None = None) -> None:
    """Point *table* at the layout of *places*, the way refresh_rows does."""
    table._tag_layout = tag_layout([place.tags for place in places])
    table._set_tag_render(width)
    table._any_comment = any(place.comment for place in places)
    table._name_width_bare = max((len(place.name) for place in places), default=0)


def _store(*places: Place) -> FleetStore:
    store = FleetStore()
    store.conn = ConnState.LIVE  # a populated fixture store means "connected"
    for place in places:
        store.places[place.name] = place
    return store


class _Harness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.cursor_events: list[str | None] = []

    def compose(self) -> ComposeResult:
        yield DeviceTable()

    def on_device_table_cursor_changed(self, m: DeviceTable.CursorChanged) -> None:
        self.cursor_events.append(m.place_name)


async def test_rows_sorted_and_unknown_capability() -> None:
    # tb-a has a wildcard resource match so the unclassified "Mystery"
    # resource attributes to it and contributes the "unknown" capability.
    store = _store(
        _place("tb-b"),
        _place("tb-a", matches=(ResourceMatchPattern("*", "*", "*"),)),
    )
    resource = Resource(
        exporter="e",
        group="g",
        name="r0",
        cls="Mystery",
        params={},
        extra={},
        acquired="",
        avail=True,
    )
    store.resources[("e", "g", "r0")] = resource
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        assert table.row_count == 2
        first = table.coordinate_to_cell_key((0, 0)).row_key.value
        assert first == "tb-a"
        caps_cell = table.get_row_at(0)[3]
        assert "?" in str(caps_cell)


async def test_resources_of_called_once_per_place_per_refresh() -> None:
    """_visible_places used to compute resources_of(place) for the filter
    check, then the row builders (column-width sizing, and the actual
    row/cell build in _rebuild_rows/_diff_update_rows) each recomputed it
    again: up to three O(places x resources) calls per place per
    refresh. refresh_rows now computes it once and threads it through."""
    store = _store(_place("tb-a"), _place("tb-b"))
    calls: list[str] = []
    original = store.resources_of

    def counting(place: Place) -> list[Resource]:
        calls.append(place.name)
        return original(place)

    store.resources_of = counting  # type: ignore[method-assign]

    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()

    assert sorted(calls) == ["tb-a", "tb-b"]


async def test_filter_and_marks() -> None:
    store = _store(_place("tb-a"), _place("tb-b"), _place("other"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.filter_query = "tb"
        table.refresh_rows(store, None)
        assert table.row_count == 2
        table.focus()
        await pilot.press("space")  # mark cursor row (tb-a)
        assert table.marks == {"tb-a"}
        assert str(table.get_row_at(0)[0]) == "●"
        await pilot.press("ctrl+a")  # mark all visible
        assert table.marks == {"tb-a", "tb-b"}
        assert table.selected_targets() == ["tb-a", "tb-b"]
        table.marks.clear()
        assert table.selected_targets() == [table.cursor_place()]


async def test_marks_pruned_and_cursor_identity() -> None:
    store = _store(_place("tb-a"), _place("tb-b"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        await pilot.press("down")  # cursor to tb-b
        table.marks.add("tb-a")
        store.places["tb-0"] = _place("tb-0")  # sorts first
        del store.places["tb-a"]
        table.refresh_rows(store, None)
        assert table.marks == set()  # tb-a gone -> pruned
        assert table.cursor_place() == "tb-b"  # identity, not index


async def test_reconnect_preserves_marks_and_cursor() -> None:
    """Regression: a reconnect's empty transient store used to prune marks
    (intersect against nothing) and lose the cursor on the following
    refresh, even though nothing the user did caused either."""
    store = _store(_place("tb-a"), _place("tb-b"))
    store.conn = ConnState.LIVE
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        await pilot.press("j")  # cursor -> tb-b
        table.marks.add("tb-a")
        assert table.cursor_place() == "tb-b"

        store.apply(ConnectionChanged(state=ConnState.CONNECTING))
        table.refresh_rows(store, None)  # stale: store.places is empty
        assert table.row_count == 0
        assert table.marks == {"tb-a"}

        store.places["tb-a"] = _place("tb-a")
        store.places["tb-b"] = _place("tb-b")
        store.conn = ConnState.LIVE
        table.refresh_rows(store, None)
        assert table.marks == {"tb-a"}
        assert table.cursor_place() == "tb-b"


async def test_columns_are_sized_for_rows_that_arrive_before_the_connection_is_live() -> None:
    """Regression: the coordinator's replay delivers every place before the
    sync that flips the connection to LIVE, so a full fleet can render
    while the store still reads as stale. Gating the column pass on that
    flag left those rows laid out against the column set on_mount built,
    whatever the terminal width was."""
    store = _store(*(_place(f"bench-{i:02d}", comment="bench in rack A, long") for i in range(8)))
    store.conn = ConnState.CONNECTING  # places in, sync not yet seen
    app = _Harness()
    async with app.run_test(size=(60, 24)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        assert table.row_count == 8
        # 60 columns has no room for Comment, live connection or not.
        assert "Comment" not in _labels(table)

        # The sync lands; nothing about the columns changes.
        store.conn = ConnState.LIVE
        table.refresh_rows(store, None)
        await pilot.pause()
        assert "Comment" not in _labels(table)


async def test_an_empty_refresh_leaves_the_columns_alone() -> None:
    """A reconnect blip empties the store for a moment. There is nothing to
    measure then, and re-measuring against nothing would flicker the header
    back to every column while the table has no rows to show."""
    store = _store(*(_place(f"bench-{i:02d}", comment="bench in rack A, long") for i in range(8)))
    app = _Harness()
    async with app.run_test(size=(60, 24)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        narrow = _labels(table)
        assert "Comment" not in narrow

        store.apply(ConnectionChanged(state=ConnState.CONNECTING))  # clears places
        table.refresh_rows(store, None)
        await pilot.pause()
        assert table.row_count == 0
        assert _labels(table) == narrow


async def test_jk_move_cursor_like_arrows() -> None:
    store = _store(_place("tb-a"), _place("tb-b"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        assert table.cursor_place() == "tb-a"
        await pilot.press("j")
        assert table.cursor_place() == "tb-b"
        await pilot.press("k")
        assert table.cursor_place() == "tb-a"


async def test_ctrl_a_toggles_mark_all() -> None:
    store = _store(_place("tb-a"), _place("tb-b"), _place("tb-c"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        await pilot.press("ctrl+a")  # none marked -> mark all
        assert table.marks == {"tb-a", "tb-b", "tb-c"}
        await pilot.press("ctrl+a")  # all marked -> unmark all
        assert table.marks == set()


async def test_ctrl_a_marks_only_filtered_visible_rows() -> None:
    store = _store(_place("tb-a"), _place("tb-b"), _place("other"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.filter_query = "tb"
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        await pilot.press("ctrl+a")
        assert table.marks == {"tb-a", "tb-b"}
        # Every visible row is already marked, so pressing ctrl+a again
        # unmarks everything, even though "other" was never a candidate
        # for marking under this filter.
        await pilot.press("ctrl+a")
        assert table.marks == set()


async def test_diff_update_preserves_cursor_and_scroll_on_unchanged_set() -> None:
    """A refresh that doesn't add/remove/reorder visible rows must not
    reset the cursor to row 0 or otherwise disturb the viewport: only
    changed cells are touched."""
    store = _store(_place("tb-a"), _place("tb-b", comment="old"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        await pilot.press("j")  # cursor -> tb-b
        assert table.cursor_coordinate.row == 1

        store.places["tb-b"] = _place("tb-b", comment="new")
        table.refresh_rows(store, None)
        await pilot.pause()

        assert table.cursor_coordinate.row == 1
        assert table.cursor_place() == "tb-b"
        assert str(table.get_row_at(1)[-1]) == "new"


async def test_diff_update_does_not_reallocate_unchanged_rows() -> None:
    """Cells that didn't change keep their cached fingerprint identity --
    only genuinely different cells trigger an update_cell call."""
    store = _store(_place("tb-a"), _place("tb-b"))
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        before = dict(table._prev_fingerprints)

        table.refresh_rows(store, None)  # nothing changed
        await pilot.pause()
        assert table._prev_fingerprints == before
        assert table._prev_visible_names == ["tb-a", "tb-b"]


async def test_g_and_shift_g_move_cursor_to_top_and_bottom() -> None:
    store = _store(*(_place(f"tb-{i:02d}") for i in range(40)))
    app = _Harness()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        await pilot.press("j", "j", "j")
        assert table.cursor_coordinate.row == 3

        await pilot.press("G")
        assert table.cursor_coordinate.row == 39
        assert table.cursor_place() == "tb-39"

        await pilot.press("g")
        assert table.cursor_coordinate.row == 0
        assert table.cursor_place() == "tb-00"


async def test_ctrl_d_and_ctrl_u_move_cursor_half_a_page() -> None:
    store = _store(*(_place(f"tb-{i:02d}") for i in range(40)))
    app = _Harness()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        assert table.cursor_coordinate.row == 0

        half = table._half_page_rows()
        assert half >= 1

        await pilot.press("ctrl+d")
        assert table.cursor_coordinate.row == half

        await pilot.press("ctrl+d")
        assert table.cursor_coordinate.row == min(39, half * 2)

        await pilot.press("ctrl+u")
        assert table.cursor_coordinate.row == min(39, half * 2) - half


async def test_ctrl_f_and_ctrl_b_move_cursor_a_full_page() -> None:
    store = _store(*(_place(f"tb-{i:02d}") for i in range(40)))
    app = _Harness()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        assert table.cursor_coordinate.row == 0

        await pilot.press("ctrl+f")
        after_one_page = table.cursor_coordinate.row
        assert after_one_page > 0

        await pilot.press("ctrl+b")
        assert table.cursor_coordinate.row == 0


async def test_h_and_l_scroll_horizontally_without_moving_cursor() -> None:
    # Comment is the lowest-priority column, so an oversized comment gets
    # dropped by the column-priority logic instead of forcing a scroll (see
    # test_device_table_responsive.py). A very long *name* forces the
    # table wider than the viewport instead: Name is a protected column
    # that is never dropped or width-limited outside -narrow.
    wide_name = "tb-" + "x" * 400
    store = _store(
        _place(wide_name),
        _place("tb-b"),
    )
    app = _Harness()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        table.focus()
        assert table.max_scroll_x > 0
        assert table.scroll_x == 0
        assert table.cursor_coordinate.row == 0

        await pilot.press("l")
        await pilot.pause()
        assert table.scroll_x > 0
        assert table.cursor_coordinate.row == 0
        after_l = table.scroll_x

        await pilot.press("h")
        await pilot.pause()
        assert table.scroll_x < after_l
        assert table.cursor_coordinate.row == 0


async def test_status_dot_is_red_and_dimmed_without_usable_resources() -> None:
    from rich.text import Text

    from labgrid_tui.ui.widgets.device_table import DOT_FREE, DOT_OFFLINE

    # tb-none has no exporter at all; tb-down has one resource that is
    # offline; tb-up has one online. Only tb-up is operable.
    pattern = (ResourceMatchPattern("e", "*", "*"),)
    store = _store(
        _place("tb-none"),
        _place("tb-down", matches=(ResourceMatchPattern("down", "*", "*"),)),
        _place("tb-up", matches=pattern),
    )
    for exporter, avail in (("down", False), ("e", True)):
        store.resources[(exporter, "g", "r")] = Resource(
            exporter=exporter,
            group="g",
            name="r",
            cls="NetworkSerialPort",
            params={},
            extra={},
            acquired="",
            avail=avail,
        )
    app = _Harness()
    async with app.run_test() as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        rows = {str(table.get_row_at(i)[1]): table.get_row_at(i) for i in range(3)}
        assert str(rows["tb-none"][2]) == DOT_OFFLINE
        assert str(rows["tb-down"][2]) == DOT_OFFLINE
        assert str(rows["tb-up"][2]) == DOT_FREE
        name_cell = rows["tb-none"][1]
        assert isinstance(name_cell, Text) and "dim" in str(name_cell.style)


async def test_tags_cell_renders_pairs_with_dimmed_keys() -> None:
    from rich.text import Text

    store = _store(
        _place("tb-a", tags={"site": "hall-a", "board": "imx8", "env": "dev"}),
        _place("tb-b"),
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        tagged = table.get_cell("tb-a", "tags")
        assert isinstance(tagged, Text)
        assert tagged.plain == "board=imx8 env=dev site=hall-a"
        dimmed = {
            tagged.plain[span.start : span.end] for span in tagged.spans if "dim" in str(span.style)
        }
        # Nothing is constant here: tb-b carries none of these keys, so the
        # keys dim and the values stay readable.
        assert dimmed == {"board=", "env=", "site="}
        # Untagged places get the same "-" every other empty cell uses.
        assert str(table.get_cell("tb-b", "tags")) == "-"


async def test_tag_pairs_line_up_across_rows_with_different_tag_sets() -> None:
    """Fleet-wide slots: the same key starts at the same column on every
    row, and a place missing a key leaves that slot blank rather than
    sliding the next pair left."""
    from rich.text import Text

    store = _store(
        _place("tb-a", tags={"board": "imx8", "owner": "ci"}),
        _place("tb-b", tags={"board": "stm32mp1", "owner": "qa", "rack": "r2"}),
        _place("tb-c", tags={"board": "am62x"}),
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        cells = {name: table.get_cell(name, "tags") for name in ("tb-a", "tb-b", "tb-c")}
        assert all(isinstance(cell, Text) for cell in cells.values())
        plain = {name: cell.plain for name, cell in cells.items() if isinstance(cell, Text)}
        assert plain["tb-a"] == "board=imx8     owner=ci"
        assert plain["tb-b"] == "board=stm32mp1 owner=qa rack=r2"
        assert plain["tb-c"] == "board=am62x"
        assert plain["tb-a"].index("owner=") == plain["tb-b"].index("owner=")


async def test_tag_layout_covers_the_fleet_not_the_filtered_rows() -> None:
    """A filter hides rows; it must not re-measure the slots underneath the
    rows that remain, or every keystroke in the filter would shuffle them."""
    from rich.text import Text

    store = _store(
        _place("tb-a", tags={"board": "imx8", "owner": "ci"}),
        _place("tb-b", tags={"board": "stm32mp1", "owner": "ci"}),
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        unfiltered = table.get_cell("tb-a", "tags")
        table.filter_query = "tb-a"
        table.refresh_rows(store, None)
        await pilot.pause()
        filtered = table.get_cell("tb-a", "tags")
        assert isinstance(unfiltered, Text) and isinstance(filtered, Text)
        # Widths still come from bench tb-b, and owner is still constant
        # across the fleet even though only one row is on screen.
        assert filtered.plain == unfiltered.plain == "board=imx8     owner=ci"


async def test_tags_shared_by_every_place_are_dimmed_whole() -> None:
    from rich.text import Text

    store = _store(
        _place("tb-a", tags={"owner": "ci", "site": "hall-a"}),
        _place("tb-b", tags={"owner": "qa", "site": "hall-a"}),
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        cell = table.get_cell("tb-a", "tags")
        assert isinstance(cell, Text)
        dimmed = {
            cell.plain[span.start : span.end] for span in cell.spans if "dim" in str(span.style)
        }
        # site is on every place with one value: the pair as a whole recedes.
        # owner separates the two places, so its value stays readable.
        assert dimmed == {"owner=", "site=hall-a"}


async def test_tags_shrink_before_any_column_is_dropped() -> None:
    """Width pressure costs Tags its own width first: the cell is cut,
    plainly and with no ellipsis, while every column keeps its place. Only
    once the cut has nothing left to give does a column go, and Comment
    is the one that goes."""
    from labgrid_tui.ui.widgets.device_table import TAGS_MIN_WIDTH

    places = [
        _place("bench-01", comment="rack A", tags={"board": "imx8", "site": "hall-a"}),
        _place("bench-02", comment="rack A", tags={"board": "am62x", "site": "hall-b"}),
    ]
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        await pilot.pause()
        _use_layout(table, places)
        rows = [table._cell_values(place, [], None, 0.0) for place in places]
        full = str(rows[0]["tags"])
        assert full == "board=imx8  site=hall-a"  # board slot fits "board=am62x"

        assert "tags" in table._visible_columns(rows, 200)
        assert table._tags_width is None  # room for every pair: no cut

        cuts: list[int] = []
        drop_width: int | None = None
        for available in range(200, 20, -1):
            keys = table._visible_columns(rows, available)
            if "comment" not in keys:
                drop_width = available
                # Comment is the first column to go, and Tags outlives it:
                # what dropping Comment frees goes back to the Tags cells.
                assert "tags" in keys
                break
            if table._tags_width is not None:
                cuts.append(table._tags_width)

        assert drop_width is not None
        assert cuts  # Tags is cut over a range of widths before Comment goes
        assert cuts[0] > cuts[-1] >= TAGS_MIN_WIDTH

        # Back to the narrowest width that still showed Comment, and render
        # a cell at the cut that width settled on. The site slot goes whole:
        # a cut never leaves half a pair behind.
        table._visible_columns(rows, drop_width + 1)
        assert table._tags_width == cuts[-1]
        cell = str(table._cell_values(places[0], [], None, 0.0)["tags"])
        assert cell == "board=imx8"
        assert full.startswith(cell)
        assert "\u2026" not in cell


async def test_constant_tag_slots_are_dropped_first_and_rows_stay_aligned() -> None:
    """Under width pressure the pairs every place shares go before anything
    that tells two places apart, and they go from every row at once."""
    places = [
        _place("bench-01", tags={"owner": "ci", "rack": "r2", "site": "hall-a"}),
        _place("bench-02", tags={"owner": "release", "site": "hall-a"}),
    ]
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        await pilot.pause()
        _use_layout(table, places)
        # site is on both places with one value; owner and rack are not.
        assert {slot.key for slot in table._tag_layout.slots if slot.constant} == {"site"}

        wide = [str(table._cell_values(place, [], None, 0.0)["tags"]) for place in places]
        assert wide == ["owner=ci      site=hall-a rack=r2", "owner=release site=hall-a"]

        _use_layout(table, places, width=24)
        narrow = [str(table._cell_values(place, [], None, 0.0)["tags"]) for place in places]
        # site went from both rows at once, so rack moved left on the row
        # that has it and still ends where the slot says it ends.
        assert narrow == ["owner=ci      rack=r2", "owner=release"]
        assert not any("site=" in cell for cell in narrow)


async def test_columns_drop_in_priority_order_and_come_back_on_widening() -> None:
    store = _store(
        *(
            _place(
                f"bench-{i:02d}",
                comment="bench in rack A with a long description",
                tags={"board": "imx8", "env": "dev", "site": "lab1"},
            )
            for i in range(4)
        )
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        assert "Tags" in _labels(table)

        dropped: list[str] = []
        for width in range(196, 44, -4):
            await pilot.resize_terminal(width, 10)
            await pilot.pause()
            labels = _labels(table)
            dropped += [
                key
                for key in ("Comment", "Tags", "Changed", "User")
                if key not in labels and key not in dropped
            ]
            for protected in ("M", "Name", "S", "Resources"):
                assert protected in labels, (width, labels)
        # Columns leave in priority order, Comment first, and Tags
        # outlives it: at 80 columns the tour still has tags to point at.
        assert dropped == ["Comment", "Tags", "Changed", "User"][: len(dropped)]
        assert dropped[0] == "Comment"

        await pilot.resize_terminal(200, 10)
        await pilot.pause()
        assert "Tags" in _labels(table)


async def test_name_cell_carries_the_aliases_dim_like_labgrid_client() -> None:
    """``labgrid-client places`` prints ``name (alias alias)``; the table
    says the same thing, with the aliases dim so the name still reads."""
    from rich.text import Text

    store = _store(
        _place("bench-01", aliases=("smoke", "ci")),
        _place("bench-02"),
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        cell = table.get_cell("bench-01", "name")
        assert isinstance(cell, Text)
        assert cell.plain == "bench-01 (smoke ci)"
        dimmed = {
            cell.plain[span.start : span.end] for span in cell.spans if "dim" in str(span.style)
        }
        assert dimmed == {" (smoke ci)"}
        # A place with no aliases is the bare name, as before.
        assert str(table.get_cell("bench-02", "name")) == "bench-02"


async def test_sorting_and_cursor_identity_ignore_the_aliases() -> None:
    """The alias is decoration on the cell; the row is still keyed and
    ordered by the place name alone."""
    store = _store(
        _place("bench-02", aliases=("aaa",)),
        _place("bench-01", aliases=("zzz",)),
    )
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        names = [table.coordinate_to_cell_key((row, 0)).row_key.value for row in range(2)]
        assert names == ["bench-01", "bench-02"]
        table.focus()
        await pilot.press("j")
        assert table.cursor_place() == "bench-02"
        assert table.selected_targets() == ["bench-02"]


async def test_narrow_name_budget_drops_the_aliases_before_eliding_the_name() -> None:
    """The name is what sorts, keys and gets pasted into a command line,
    so the -narrow cell budget spends itself on the aliases first."""
    from labgrid_tui.ui.widgets.device_table import NAME_MAX_WIDTH_NARROW

    app = _Harness()
    async with app.run_test(size=(70, 10)) as pilot:  # -narrow
        table = app.query_one(DeviceTable)
        await pilot.pause()

        fits = _place("bench-01", aliases=("sm",))  # 8 + " (sm)" = 13
        assert table._name_plain(fits, aliases=True) == "bench-01 (sm)"

        # Both of these blow the budget, so the aliases go whole; the name
        # itself is only elided when it is the part that does not fit.
        long_alias = _place("bench-02", aliases=("a-much-longer-alias",))
        assert table._name_plain(long_alias, aliases=True) == "bench-02"
        long_name = _place("bench-a-rather-long-name", aliases=("x",))
        elided = table._name_plain(long_name, aliases=True)
        assert "(" not in elided
        assert len(elided) <= NAME_MAX_WIDTH_NARROW
        assert "…" in elided


async def test_comment_column_appears_only_when_a_place_carries_one() -> None:
    """A fleet with no comments anywhere has room for the column and still
    does not get it: a labelled strip of blanks is not worth a column.
    Width is not what decides this, so the check runs at 150 columns,
    where every column fits several times over."""
    places = [_place(f"bench-{i:02d}", tags={"board": "imx8"}) for i in range(9)]
    store = _store(*places)
    app = _Harness()
    async with app.run_test(size=(150, 30)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        labels = _labels(table)
        assert "Comment" not in labels, labels
        # Everything else is there, so the drop was the rule, not pressure.
        assert "Tags" in labels and "Changed" in labels and "User" in labels
        assert table._tags_width is None
        assert table._show_aliases

        # One comment anywhere in the fleet brings the column back.
        store.places["bench-04"] = _place("bench-04", comment="rack A", tags={"board": "imx8"})
        table.refresh_rows(store, None)
        await pilot.pause()
        assert "Comment" in _labels(table)
        assert str(table.get_cell("bench-04", "comment")) == "rack A"
        assert str(table.get_cell("bench-00", "comment")) == ""


async def test_comment_visibility_follows_the_fleet_not_the_filter() -> None:
    """Typing in the filter must not add or remove a column under the
    reader, so the rule reads the whole fleet, not the visible rows."""
    store = _store(_place("bench-01", comment="rack A"), _place("bench-02"))
    app = _Harness()
    async with app.run_test(size=(150, 30)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        assert "Comment" in _labels(table)

        table.filter_query = "bench-02"  # the only visible row has no comment
        table.refresh_rows(store, None)
        await pilot.pause()
        assert table.row_count == 1
        assert "Comment" in _labels(table)


async def test_aliases_are_the_first_thing_width_pressure_takes() -> None:
    """An alias is a second name for a place the row already names, so it
    goes before a tag pair is cut or a column is dropped."""
    places = [
        _place("bench-01", comment="rack A", tags={"board": "imx8"}, aliases=("smoke",)),
        _place("bench-02", comment="rack A", tags={"board": "am62x"}, aliases=("bringup",)),
    ]
    store = _store(*places)
    app = _Harness()
    async with app.run_test(size=(200, 10)) as pilot:
        table = app.query_one(DeviceTable)
        table.refresh_rows(store, None)
        await pilot.pause()
        assert table._show_aliases
        assert str(table.get_cell("bench-01", "name")) == "bench-01 (smoke)"

        alias_drop: int | None = None
        for width in range(200, 60, -1):
            await pilot.resize_terminal(width, 10)
            await pilot.pause()
            if not table._show_aliases:
                alias_drop = width
                break
            # Nothing else has given anything up yet.
            assert table._tags_width is None, width
            assert "Comment" in _labels(table), width

        assert alias_drop is not None
        assert str(table.get_cell("bench-01", "name")) == "bench-01"

        # Widening puts them back: the decision is width, not a latch.
        await pilot.resize_terminal(200, 10)
        await pilot.pause()
        assert table._show_aliases
        assert str(table.get_cell("bench-01", "name")) == "bench-01 (smoke)"


def _labels(table: DeviceTable) -> list[str]:
    return [str(column.label) for column in table.ordered_columns]
