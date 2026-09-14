from textual.app import App, ComposeResult

from labgrid_tui.coordinator.models import Place, Resource, ResourceMatchPattern
from labgrid_tui.coordinator.stream import ConnectionChanged, ConnState
from labgrid_tui.ui.store import FleetStore
from labgrid_tui.ui.widgets.device_table import DeviceTable


def _place(name: str, comment: str = "", acquired: str | None = None,
           tags: dict[str, str] | None = None,
           matches: tuple[ResourceMatchPattern, ...] = ()) -> Place:
    return Place(name=name, aliases=(), comment=comment, tags=tags or {},
                 matches=matches, acquired=acquired, acquired_resources=(), allowed=(),
                 created=0.0, changed=0.0, reservation=None)


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
    resource = Resource(exporter="e", group="g", name="r0", cls="Mystery",
                        params={}, extra={}, acquired="", avail=True)
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
        await pilot.press("space")           # mark cursor row (tb-a)
        assert table.marks == {"tb-a"}
        assert str(table.get_row_at(0)[0]) == "●"
        await pilot.press("ctrl+a")          # mark all visible
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
        await pilot.press("down")            # cursor to tb-b
        table.marks.add("tb-a")
        store.places["tb-0"] = _place("tb-0")  # sorts first
        del store.places["tb-a"]
        table.refresh_rows(store, None)
        assert table.marks == set()          # tb-a gone -> pruned
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
        await pilot.press("ctrl+a")           # none marked -> mark all
        assert table.marks == {"tb-a", "tb-b", "tb-c"}
        await pilot.press("ctrl+a")           # all marked -> unmark all
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
            exporter=exporter, group="g", name="r", cls="NetworkSerialPort",
            params={}, extra={}, acquired="", avail=avail,
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
