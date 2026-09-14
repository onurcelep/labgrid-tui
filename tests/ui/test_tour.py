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
from textual.widgets import Input

from labgrid_tui.coordinator.stream import ConnState
from labgrid_tui.tour.app import TourApp
from labgrid_tui.ui.screens.command_overlay import CommandOverlay
from labgrid_tui.ui.screens.coordinator_delete import CoordinatorDeleteConfirm
from labgrid_tui.ui.screens.coordinator_edit import CoordinatorEditModal
from labgrid_tui.ui.screens.coordinator_selector import CoordinatorSelector
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
    """Every "n" advances exactly one step (TourController's one-trigger,
    one-advance invariant, see tour/steps.py): asserted after each press
    with a single pilot.pause(), not a timing-dependent wait."""
    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        panel = _panel(app)
        assert panel.current_text.startswith("Step 1/8")
        for expected in range(2, 9):
            await pilot.press("n")
            await pilot.pause()
            assert panel.current_text.startswith(f"Step {expected}/8")
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("Done.")


async def test_n_skips_even_while_coordinator_selector_is_open() -> None:
    """Regression: CoordinatorSelector binds its own "n" to "new
    coordinator" (see coordinator_selector.py). Without priority=True on
    the tour's binding, step 8's "n" (the hint TourPanel shows on every
    step) would silently open that dialog instead of skipping."""
    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        panel = _panel(app)
        for expected in range(2, 9):
            await pilot.press("n")
            await pilot.pause()
            assert panel.current_text.startswith(f"Step {expected}/8")

        await pilot.press("P")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorSelector)
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("Done.")
        assert not isinstance(app.screen, CoordinatorEditModal)


async def test_full_step_sequence_advances_the_panel_by_keyboard_alone() -> None:
    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        panel = _panel(app)
        table = app.screen.query_one(DeviceTable)
        assert panel.current_text.startswith("Step 1/8")

        # Step 1: move the cursor down to bench-02 (never reserved or
        # acquired by the script, so step 3 below can acquire it directly).
        await pilot.press("j")
        await pilot.pause()
        assert panel.current_text.startswith("Step 2/8")
        assert table.cursor_place() == "bench-02"

        # Step 2: mark the cursor row.
        await pilot.press("space")
        await pilot.pause()
        assert panel.current_text.startswith("Step 3/8")

        # Step 3: acquire it (copy-only: never actually mutates the store).
        await pilot.press("r")
        await pilot.pause()
        assert panel.current_text.startswith("Step 4/8")

        # Step 4: open commands, copy whatever is highlighted.
        await pilot.press("c")
        await pilot.pause()
        assert isinstance(app.screen, CommandOverlay)
        await pilot.press("enter")
        await pilot.pause()
        assert panel.current_text.startswith("Step 5/8")
        await pilot.press("escape")
        await pilot.pause()

        # Step 5: open the detail view, then close it.
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert panel.current_text.startswith("Step 6/8")

        # Step 6: alice's scripted acquire (design +4s) landed somewhere
        # during steps 1-5 above (TourApp._on_stream_event calls
        # TourController.on_alice_acquire the moment it lands, but that
        # only advances the step if step 6 is already current, see
        # tour/steps.py); "n" is the documented way to move on once
        # you've seen it in the log, same as a real user would.
        await pilot.press("n")
        await pilot.pause()
        assert panel.current_text.startswith("Step 7/8")

        # Step 7: open commands, switch to the robot pack tab, copy an entry.
        await pilot.press("c")
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert panel.current_text.startswith("Step 8/8")
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
    """Exercises every coordinator-registry write path the real app has
    (create, edit, delete, switch) plus the ui-state persist path, all
    inside the tour, then asserts nothing landed under the isolated
    XDG_CONFIG_HOME/XDG_STATE_HOME: LabgridTuiApp.persist_coordinators and
    persist_ui_state=False must absorb every one of them."""
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    state_home = Path(os.environ["XDG_STATE_HOME"])

    app = TourApp(speed=SPEED)
    async with app.run_test(size=(120, 30)) as pilot:
        assert await _wait_until(pilot, lambda: len(app.store.places) == 6)
        await pilot.press("j")
        await pilot.press("a")  # toggles the activity log, persisted on the real app

        # Create: "n" is shadowed by the tour's own skip binding (see
        # TourApp.BINDINGS), so drive CoordinatorSelector's create action
        # directly instead of pressing its usual key.
        await pilot.press("P")
        await pilot.pause()
        selector = app.screen
        assert isinstance(selector, CoordinatorSelector)
        selector.action_new_coordinator()
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorEditModal)
        await pilot.press(*"extra")
        await pilot.press("enter")
        await pilot.press(*"127.0.0.1:9999")
        await pilot.press("enter")
        await pilot.press("enter")  # empty prefix, submits
        await pilot.pause()
        assert "extra" in app.coordinators.entries

        # Edit: change the address of the coordinator just created.
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("up")  # entries sorted [desk, extra, lab]; land on "extra"
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorEditModal)
        address_input = app.screen.query_one("#coord-edit-address", Input)
        address_input.clear()
        await pilot.press(*"127.0.0.1:8888")
        await pilot.press("enter")
        await pilot.press("enter")
        await pilot.pause()
        assert app.coordinators.entries["extra"].address == "127.0.0.1:8888"

        # Delete: remove the entry just created and edited.
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        await pilot.press("x")
        await pilot.pause()
        assert isinstance(app.screen, CoordinatorDeleteConfirm)
        await pilot.press("y")
        await pilot.pause()
        assert "extra" not in app.coordinators.entries

        # Switch: a real round trip to desk and back (unrelated to the
        # entry just deleted).
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("k")  # entries [desk, lab], current "lab": up to "desk"
        await pilot.press("enter")
        assert await _wait_until(pilot, lambda: set(app.store.places) == {"desk-01", "desk-02"})
        await pilot.press("P")
        await pilot.pause()
        await pilot.press("j")  # "desk" (now active) down to "lab"
        await pilot.press("enter")
        assert await _wait_until(
            pilot, lambda: set(app.store.places) == {f"bench-0{i}" for i in range(1, 7)}
        )

    assert not config_home.exists() or not any(config_home.rglob("*"))
    assert not state_home.exists() or not any(state_home.rglob("*"))
