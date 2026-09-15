"""`labgrid-tui tour`: a guided walkthrough on fake data, no coordinator
required.

Reuses every seam LabgridTuiApp exposes for a downstream shell (FleetSource,
ActionRunner, in-memory config/coordinators/packs/ui-state injection)
instead of forking the dashboard: the tour is a thin subclass, not a
parallel implementation.
"""

from collections.abc import Callable

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.css.query import NoMatches
from textual.screen import Screen
from textual.widgets import Footer

from labgrid_tui.config import Config
from labgrid_tui.coordinators import CoordinatorEntry, Coordinators
from labgrid_tui.tour.fleet import ScriptedFleet, fleet_source_factory
from labgrid_tui.tour.pack import load_robot_pack
from labgrid_tui.tour.runner import TourActionRunner
from labgrid_tui.tour.steps import DELAYED_ENTRY_CUES, ENTRY_CUES, TourController
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.dashboard import DashboardScreen, TourHook
from labgrid_tui.ui.uistate import UiState
from labgrid_tui.ui.widgets.tour_panel import TourPanel

SUB_TITLE = "TOUR (fake data)"
LAB_ADDRESS = "tour:lab"
DESK_ADDRESS = "tour:desk"

# How long the closing line stays after the last step; any key ends it.
DONE_SECONDS = 6.0
# Delay before a step's "a moment later" cue (see steps.DELAYED_ENTRY_CUES).
DELAYED_CUE_SECONDS = 2.5
WELCOME_TITLE = "Welcome to labgrid-tui"
WELCOME_TEXT = (
    "A tour on fake data: six benches, nothing here reaches a real lab. "
    "Follow the bar above the footer; [b]n[/] skips a step, [b]q[/] quits."
)


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
    def __init__(
        self,
        runner: ActionRunner,
        ui_state: UiState,
        persist: Callable[[], None],
        tour_hook: TourHook,
        controller: TourController,
    ) -> None:
        super().__init__(runner, ui_state, persist, tour_hook=tour_hook)
        self._controller = controller

    def compose(self) -> ComposeResult:
        # The panel is a normal flow widget placed before the docked Footer,
        # so it takes the row above the key hints rather than covering them.
        for widget in super().compose():
            if isinstance(widget, Footer):
                yield TourPanel(self._controller)
            yield widget


class TourApp(LabgridTuiApp):
    BINDINGS = [
        *LabgridTuiApp.BINDINGS,
        # priority=True: the last step opens CoordinatorSelector, which binds
        # "n" to "new coordinator" (see coordinator_selector.py); without
        # priority, that screen-level binding would win and "skip" (which
        # TourPanel advertises on every step) would silently do nothing
        # while that modal is on top.
        Binding("n", "tour_skip", "Skip step", show=True, priority=True),
    ]

    def __init__(self) -> None:
        self._controller = TourController()
        self._controller.on_enter = self._on_step_entered
        self._controller.on_done = self._on_done
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
        )

    def on_mount(self) -> None:
        super().on_mount()
        self.notify(WELCOME_TEXT, title=WELCOME_TITLE, timeout=10)

    def _on_tour_hook(self, name: str, detail: str) -> None:
        if name == "cursor_changed":
            self._controller.on_cursor_changed(detail)
        elif name == "detail_open":
            self._controller.on_detail_open()
        elif name == "detail_close":
            self._controller.on_detail_close()

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

    def _on_done(self) -> None:
        self.set_timer(DONE_SECONDS, self.dismiss_tour_panel)

    def dismiss_tour_panel(self) -> None:
        try:
            self.screen_stack[0].query_one(TourPanel).remove()
        except NoMatches:
            return

    def on_key(self, event: events.Key) -> None:
        # Any key after the closing line ends the tour chrome; the key
        # itself still reaches whatever it was meant for.
        if self._controller.done:
            self.dismiss_tour_panel()

    def action_tour_skip(self) -> None:
        if self._controller.done:
            self.dismiss_tour_panel()
        else:
            self._controller.skip()


def run_tour() -> None:
    TourApp().run()
