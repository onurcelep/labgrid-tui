"""`labgrid-tui tour`: a guided walkthrough on fake data, no coordinator
required.

Reuses every seam LabgridTuiApp exposes for a downstream shell (FleetSource,
ActionRunner, in-memory config/coordinators/packs/ui-state injection)
instead of forking the dashboard: the tour is a thin subclass, not a
parallel implementation. Its own chrome is a welcome card, a floating step
card over the fleet table, the same step text inside any modal on top, and
an arrow next to whatever the step is about.
"""

import contextlib
from collections.abc import Callable

from textual import events
from textual.binding import Binding
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.screen import Screen

from labgrid_tui.config import Config
from labgrid_tui.coordinators import CoordinatorEntry, Coordinators
from labgrid_tui.tour.fleet import ScriptedFleet, fleet_source_factory
from labgrid_tui.tour.pack import load_robot_pack
from labgrid_tui.tour.runner import TourActionRunner
from labgrid_tui.tour.steps import (
    DELAYED_ENTRY_CUES,
    ENTRY_CUES,
    POINT_LOG,
    POINT_STATUS,
    POINT_TABLE,
    TourController,
)
from labgrid_tui.tour.welcome import WelcomeScreen
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.guidance import MARKER, TourGuidance
from labgrid_tui.ui.screens.dashboard import DashboardScreen, TourHook
from labgrid_tui.ui.uistate import UiState
from labgrid_tui.ui.widgets.activity_log import ActivityLog
from labgrid_tui.ui.widgets.device_table import DeviceTable
from labgrid_tui.ui.widgets.status_bar import StatusBar
from labgrid_tui.ui.widgets.tour_card import CARD_TOP, CARD_WIDTH, TourCard

SUB_TITLE = "TOUR (fake data)"
LAB_ADDRESS = "tour:lab"
DESK_ADDRESS = "tour:desk"

# Delay before a step's "a moment later" cue (see steps.DELAYED_ENTRY_CUES).
DELAYED_CUE_SECONDS = 2.5


def _tour_config() -> Config:
    return Config(
        coordinator=LAB_ADDRESS,
        coordinator_source="tour",
        prefix=None,
        capability_overrides={},
        proxy_set=False,
    )


def _tour_coordinators() -> Coordinators:
    return Coordinators(
        current="lab",
        entries={
            "lab": CoordinatorEntry(name="lab", address=LAB_ADDRESS),
            "desk": CoordinatorEntry(name="desk", address=DESK_ADDRESS),
        },
    )


class TourDashboardScreen(DashboardScreen):
    DEFAULT_CSS = """
    /* The card lives on its own layer inside the table row: absolutely
       positioned there, it floats over free table space and the table
       keeps its full size. */
    TourDashboardScreen #main-row { layers: base tour; }
    TourDashboardScreen #main-row > DeviceTable { layer: base; }
    """

    def __init__(
        self,
        runner: ActionRunner,
        ui_state: UiState,
        persist: Callable[[], None],
        tour_hook: TourHook,
        controller: TourController,
        guidance_provider: Callable[[], TourGuidance | None],
    ) -> None:
        super().__init__(
            runner, ui_state, persist, tour_hook=tour_hook, guidance_provider=guidance_provider
        )
        self._controller = controller

    def show_card(self) -> TourCard:
        card = TourCard(self._controller)
        self.query_one("#main-row", Horizontal).mount(card)
        self.call_after_refresh(self.place_card)
        return card

    def place_card(self) -> None:
        try:
            card = self.query_one(TourCard)
            row = self.query_one("#main-row", Horizontal)
        except NoMatches:
            return
        width, height = row.content_size
        x = max(0, width - CARD_WIDTH - 1)
        # Below the fake benches when the table is tall enough; otherwise as
        # low as still fits, so the card never runs past the table row.
        y = max(0, min(CARD_TOP, height - card.outer_size.height))
        card.styles.offset = (x, y)

    def on_resize(self, event: events.Resize) -> None:
        super().on_resize(event)
        self.place_card()

    def set_marker(self, target: str | None) -> None:
        """Put the tour pointer on the table's cursor row, the activity log,
        the status bar, or nowhere."""
        with contextlib.suppress(NoMatches):
            self.query_one(DeviceTable).set_pointer(MARKER if target == POINT_TABLE else None)
        with contextlib.suppress(NoMatches):
            log = self.query_one(ActivityLog)
            log.border_title = f"{MARKER} activity" if target == POINT_LOG else ""
        with contextlib.suppress(NoMatches):
            self.query_one(StatusBar).set_marker(MARKER if target == POINT_STATUS else "")


class TourApp(LabgridTuiApp):
    BINDINGS = [
        *LabgridTuiApp.BINDINGS,
        # priority=True: the last step opens CoordinatorSelector, which binds
        # "n" to "new coordinator" (see coordinator_selector.py); without
        # priority, that screen-level binding would win and "skip" (which the
        # footer advertises on every step) would silently do nothing while
        # that modal is on top.
        Binding("n", "tour_skip", "Skip step", show=True, priority=True),
    ]

    def __init__(self) -> None:
        self._controller = TourController()
        self._controller.on_change = self._on_step_changed
        self._controller.on_enter = self._on_step_entered
        self.started = False
        super().__init__(
            _tour_config(),
            coordinators=_tour_coordinators(),
            persist_coordinators=False,
            packs=[load_robot_pack()],
            pack_errors=[],
            # The activity log is part of the story (step 5 points at it),
            # so it starts visible regardless of the user's real preference.
            ui_state=UiState(onboarded=True, show_activity=True),
            persist_ui_state=False,
            fleet_source_factory=fleet_source_factory(),
            runner_factory=self._build_runner,
            sub_title=SUB_TITLE,
            tour_hook=self._on_tour_hook,
            on_coordinator_switch=self._controller.on_coordinator_switch,
        )
        self.extra_status_segments.append(lambda: "tour")

    def _build_runner(self, app: LabgridTuiApp) -> ActionRunner:
        return TourActionRunner(
            copy_to_clipboard=app.copy_to_clipboard,
            spawn=app._spawn_worker,
            activity=app.push_event,
            output=app._emit_output,
            notify=lambda message: app.notify(message, severity="warning"),
            on_copy=self._controller.on_copy,
        )

    def get_default_screen(self) -> Screen[None]:
        return TourDashboardScreen(
            self.runner,
            self._ui_state,
            self._persist_ui_state,
            self._on_tour_hook,
            self._controller,
            self.guidance,
        )

    @property
    def dashboard(self) -> TourDashboardScreen:
        screen = self.screen_stack[0]
        assert isinstance(screen, TourDashboardScreen)
        return screen

    def guidance(self) -> TourGuidance | None:
        """What a modal pushed right now should show; nothing before the tour starts."""
        if not self.started:
            return None
        return TourGuidance(
            text=f"{self._controller.title()}: {self._controller.label()}",
            target=self._controller.modal_target(),
        )

    def on_mount(self) -> None:
        super().on_mount()
        self.push_screen(WelcomeScreen(), callback=self._start_tour)

    def _start_tour(self, _result: None) -> None:
        self.started = True
        self.dashboard.show_card()
        self.dashboard.set_marker(self._controller.dashboard_marker())

    def _on_tour_hook(self, name: str, detail: str) -> None:
        if name == "cursor_changed":
            self._controller.on_cursor_changed(detail)
        elif name == "detail_open":
            self._controller.on_detail_open()
        elif name == "detail_close":
            self._controller.on_detail_close()

    def _on_step_changed(self, _label: str) -> None:
        with contextlib.suppress(NoMatches):
            self.dashboard.query_one(TourCard).refresh_text()
        self.dashboard.place_card()
        self.dashboard.set_marker(self._controller.dashboard_marker())
        update = getattr(self.screen, "update_guidance", None)
        if callable(update):
            update(self.guidance())

    def _on_step_entered(self, step: int) -> None:
        for cue in ENTRY_CUES.get(step, ()):
            self._cue(cue)
        for cue in DELAYED_ENTRY_CUES.get(step, ()):
            self.set_timer(self.delayed_cue_seconds, lambda cue=cue: self._cue(cue))

    delayed_cue_seconds: float = DELAYED_CUE_SECONDS

    def _cue(self, cue: str) -> None:
        source = self.fleet_source
        if not isinstance(source, ScriptedFleet):
            return
        params = {"place": self._controller.acquired_place or "bench-01"}
        if source.cue(cue, **params):
            # Reservation state is only observable through the poll; run it
            # now so the Reservations tab and the "(yours)" marker follow
            # the cue instead of the next 10 s tick.
            self.run_worker(self._poll_reservations(), exclusive=False)

    def action_tour_skip(self) -> None:
        # App-level priority binding: it fires before the welcome card's own
        # "n", so starting the tour is this action's job there.
        if not self.started:
            if isinstance(self.screen, WelcomeScreen):
                self.screen.action_start()
            return
        self._controller.skip()


def run_tour() -> None:
    TourApp().run()
