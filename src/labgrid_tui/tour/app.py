"""`labgrid-tui tour`: a guided walkthrough on fake data, no coordinator
required.

Reuses every seam LabgridTuiApp exposes for a downstream shell (FleetSource,
ActionRunner, in-memory config/coordinators/packs/ui-state injection)
instead of forking the dashboard: the tour is a thin subclass, not a
parallel implementation. Its own chrome is a welcome card, a step card
anchored to whatever the step is about, and the same step text inside any
modal on top. The dashboard spotlights the step's widget by dimming the
others, so nothing is ever drawn over the app itself.
"""

import contextlib

from textual.binding import Binding
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
    TourController,
)
from labgrid_tui.tour.welcome import WelcomeScreen
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.guidance import TourGuidance
from labgrid_tui.ui.screens.dashboard import DashboardScreen
from labgrid_tui.ui.uistate import UiState
from labgrid_tui.ui.widgets.tour_card import TourCard

SUB_TITLE = "TOUR (fake data)"
LAB_ADDRESS = "tour:lab"
DESK_ADDRESS = "tour:desk"

# Unit of delay for a step's follow-on cues (see steps.DELAYED_ENTRY_CUES).
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
        return DashboardScreen(
            self.runner,
            self._ui_state,
            self._persist_ui_state,
            tour_hook=self._on_tour_hook,
            guidance_provider=self.guidance,
        )

    @property
    def dashboard(self) -> DashboardScreen:
        screen = self.screen_stack[0]
        assert isinstance(screen, DashboardScreen)
        return screen

    def guidance(self) -> TourGuidance | None:
        """What a modal pushed right now should show; nothing before the tour starts."""
        if not self.started:
            return None
        return TourGuidance(title=self._controller.title(), text=self._controller.label())

    def on_mount(self) -> None:
        super().on_mount()
        self.push_screen(WelcomeScreen(), callback=self._start_tour)

    def _start_tour(self, _result: None) -> None:
        self.started = True
        self._show_step(self.dashboard.show_tour_card())

    def _on_tour_hook(self, name: str, detail: str) -> None:
        if name == "cursor_changed":
            self._controller.on_cursor_changed(detail)
        elif name == "detail_open":
            self._controller.on_detail_open()
        elif name == "detail_close":
            self._controller.on_detail_close()

    def _on_step_changed(self, _label: str) -> None:
        with contextlib.suppress(NoMatches):
            self._show_step(self.dashboard.query_one(TourCard))
        update = getattr(self.screen, "update_guidance", None)
        if callable(update):
            update(self.guidance())

    def _show_step(self, card: TourCard) -> None:
        card.show(self._controller.title(), self._controller.label())
        self.dashboard.set_tour_target(self._controller.dashboard_target())

    def _on_step_entered(self, step: int) -> None:
        for cue in ENTRY_CUES.get(step, ()):
            self._cue(cue)
        for multiple, cue in DELAYED_ENTRY_CUES.get(step, ()):
            self.set_timer(self.delayed_cue_seconds * multiple, lambda cue=cue: self._cue(cue))

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
