from textual.app import App, ComposeResult

from labgrid_tui.coordinator.models import Place
from labgrid_tui.ui.widgets.filter_bar import FilterBar, matches_filter


def _place(name: str = "tb-1", comment: str = "", tags: dict[str, str] | None = None) -> Place:
    return Place(
        name=name,
        aliases=(),
        comment=comment,
        tags=tags or {},
        matches=(),
        acquired=None,
        acquired_resources=(),
        allowed=(),
        created=0.0,
        changed=0.0,
        reservation=None,
    )


def test_empty_query_matches() -> None:
    assert matches_filter(_place(), set(), "")
    assert matches_filter(_place(), set(), "   ")


def test_matches_fields() -> None:
    place = _place(name="board-a-1", comment="Bench A", tags={"board": "imx8"})
    assert matches_filter(place, {"power"}, "board-a")
    assert matches_filter(place, {"power"}, "bench a")
    assert matches_filter(place, {"power"}, "board=imx8")
    assert matches_filter(place, {"power"}, "POWER")
    assert not matches_filter(place, {"power"}, "console")


def test_matches_tags_as_pair_key_or_value() -> None:
    """The Tags column shows key=value pairs, so the filter takes any part
    of one: the whole pair as typed, the bare key, or the bare value."""
    place = _place(name="bench-01", tags={"board": "imx8", "site": "lab1"})
    assert matches_filter(place, set(), "board=imx8")
    assert matches_filter(place, set(), "board")
    assert matches_filter(place, set(), "imx8")
    assert matches_filter(place, set(), "LAB1")
    assert not matches_filter(place, set(), "board=rpi4")
    assert not matches_filter(place, set(), "lab2")


class _Harness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.queries: list[str] = []

    def compose(self) -> ComposeResult:
        yield FilterBar()

    def on_filter_bar_filter_changed(self, message: FilterBar.FilterChanged) -> None:
        self.queries.append(message.query)


async def test_filter_changed_message() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        app.query_one(FilterBar).focus()
        await pilot.press("t", "b")
        await pilot.pause()
    assert app.queries[-1] == "tb"
