import logging

import pytest
from textual.app import App, ComposeResult

from labgrid_tui.ui.widgets.status_bar import Segment, StatusBar


class _Harness(App[None]):
    def __init__(self) -> None:
        super().__init__()
        self.mood: str | None = "live"

    def compose(self) -> ComposeResult:
        yield StatusBar(
            [
                Segment(lambda: "coord (env)"),
                Segment(lambda: self.mood),
                Segment(lambda: None),
            ]
        )


async def test_segments_render_and_refresh() -> None:
    app = _Harness()
    async with app.run_test() as pilot:
        bar = app.query_one(StatusBar)
        bar.refresh_status()
        await pilot.pause()
        assert str(bar.render()) == "coord (env) | live"
        app.mood = None
        bar.refresh_status()
        await pilot.pause()
        assert str(bar.render()) == "coord (env)"


def _raising_provider() -> str | None:
    raise RuntimeError("boom: extension segment blew up")


class _RaisingHarness(App[None]):
    def compose(self) -> ComposeResult:
        yield StatusBar(
            [
                Segment(lambda: "coord (env)"),
                Segment(_raising_provider),
                Segment(lambda: "live"),
            ]
        )


async def test_raising_segment_provider_is_dropped_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A downstream extra_status_segments provider raising must not break
    the other segments, and the failure must be visible in the logs.
    """
    app = _RaisingHarness()
    async with app.run_test() as pilot:
        bar = app.query_one(StatusBar)
        with caplog.at_level(logging.WARNING, logger="labgrid_tui.ui.widgets.status_bar"):
            bar.refresh_status()
            await pilot.pause()
        assert str(bar.render()) == "coord (env) | live"
        assert "boom: extension segment blew up" in caplog.text
