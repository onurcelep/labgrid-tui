"""Pilot tests for `labgrid-tui tour`: no network, in-memory everything,
every step's completion trigger reachable by keyboard alone.

SPEED scales the fleet's whole ~22s scripted timeline down to well under a
second of wall-clock time (see tests/tour/test_fleet.py for the fleet's own
timing tests); _wait_until below polls instead of sleeping fixed amounts.
"""

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import grpc.aio
import pytest

from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.tour.app import TourApp
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.tour_panel import TourPanel

SPEED = 250.0


@pytest.fixture(autouse=True)
def _no_grpc_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tour must never touch the network: proves it by making the one
    function a real connection would need blow up if ever called."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("the tour must never open a gRPC channel")

    monkeypatch.setattr(grpc.aio, "insecure_channel", _boom)


async def _wait_until(pilot: object, predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        await pilot.pause()  # type: ignore[attr-defined]
        if predicate():
            return True
        if asyncio.get_running_loop().time() > deadline:
            return False
        await asyncio.sleep(0.02)


def _panel(app: TourApp) -> TourPanel:
    return app.screen.query_one(TourPanel)


async def test_tour_starts_live_on_fake_data_with_no_network() -> None:
    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: app.store.conn is ConnState.LIVE)
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        assert app.sub_title == "TOUR (fake data)"
        assert _panel(app).current_text.startswith("Step 1/8")


async def test_n_skips_one_step_at_a_time() -> None:
    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        panel = _panel(app)
        assert panel.current_text.startswith("Step 1/8")
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("Step 2/8")
        for _ in range(6):
            await pilot.press("n")
            await pilot.pause()
        assert panel.current_text.startswith("Done.")


async def test_full_step_sequence_advances_the_panel_by_keyboard_alone() -> None:
    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        panel = _panel(app)
        table = app.screen.query_one(DeviceTable)
        assert panel.current_text.startswith("Step 1/8")

        # Step 1: move the cursor (bench-01 -> bench-02, then back to
        # bench-01 so step 3 acquires an unreserved, unacquired place).
        await pilot.press("j")
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Step 2/8"))
        await pilot.press("k")
        await pilot.pause()
        assert table.cursor_place() == "bench-01"

        # Step 2: mark the cursor row.
        await pilot.press("space")
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Step 3/8"))

        # Step 3: acquire it (copy-only: never actually mutates the store).
        await pilot.press("r")
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Step 4/8"))

        # Step 4: open commands, copy whatever is highlighted.
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Step 5/8"))
        await pilot.press("escape")
        await pilot.pause()

        # Step 5: open the detail view, then close it.
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        # Step 6 auto-advances the moment alice's scripted acquire lands
        # (very likely already true by now at SPEED); either way step 7
        # is reached with no further input.
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Step 7/8"))

        # Step 7: open commands, switch to the robot pack tab, copy an entry.
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Step 8/8"))
        await pilot.press("escape")
        await pilot.pause()

        # Step 8: switch to desk, then back to lab.
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("k")  # from "lab" (active) up to "desk"
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: set(app.store.places) == {"desk-01", "desk-02"})

        await pilot.press("P")
        await pilot.pause()
        await pilot.press("j")  # from "desk" (now active) down to "lab"
        await pilot.press("enter")
        assert await _wait_until(
            pilot, lambda: set(app.store.places) == {f"bench-0{i}" for i in range(1, 7)}
        )
        assert await _wait_until(pilot, lambda: panel.current_text.startswith("Done."))


async def test_q_quits_the_tour() -> None:
    app = TourApp(speed=SPEED)
    exit_calls: list[object] = []
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        app.exit = lambda *a, **kw: exit_calls.append((a, kw))  # type: ignore[method-assign]
        await pilot.press("q")
        await pilot.pause()
    assert len(exit_calls) == 1


@pytest.mark.first_run  # skip conftest's own onboarded=True seed write, see below
async def test_tour_never_touches_the_isolated_xdg_dirs() -> None:
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    state_home = Path(os.environ["XDG_STATE_HOME"])

    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        # Exercise a few actions that touch config/coordinators/ui-state on
        # the real app, to prove the isolated in-memory versions absorb them.
        await pilot.press("j")
        await pilot.press("a")  # toggles the activity log, persisted on the real app
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()

    assert not config_home.exists() or not any(config_home.rglob("*"))
    assert not state_home.exists() or not any(state_home.rglob("*"))
