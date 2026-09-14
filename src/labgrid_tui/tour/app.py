"""`labgrid-tui tour`: a guided walkthrough on fake data, no coordinator
required.

Reuses every seam LabgridTuiApp exposes for a downstream shell (FleetSource,
ActionRunner, in-memory config/coordinators/packs/ui-state injection)
instead of forking the dashboard: the tour is a thin subclass, not a
parallel implementation.
"""

from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen

from labgrid_tui.config import Config
from labgrid_tui.coordinator.stream import Event, PlaceChanged
from labgrid_tui.coordinators import CoordinatorEntry, Coordinators
from labgrid_tui.tour.fleet import fleet_source_factory
from labgrid_tui.tour.pack import load_robot_pack
from labgrid_tui.tour.runner import TourActionRunner
from labgrid_tui.tour.steps import TourController
from labgrid_tui.ui.actions import ActionRunner
from labgrid_tui.ui.app import LabgridTuiApp
from labgrid_tui.ui.screens.dashboard import DashboardScreen, TourHook
from labgrid_tui.ui.uistate import UiState
from labgrid_tui.ui.widgets.tour_panel import TourPanel

SUB_TITLE = "TOUR (fake data)"
LAB_ADDRESS = "tour:lab"
DESK_ADDRESS = "tour:desk"

# Alice's acquire of bench-03 (see tour.fleet.LAB_SCRIPT) is the one
# scripted event a tour step (watching the activity log) waits on directly,
# rather than through a dashboard/runner hook.
_WATCHED_PLACE = "bench-03"


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
        # Composed after Footer: widgets docked to the same edge stack
        # innermost-last, so this lands just above the footer instead of
        # underneath it.
        yield from super().compose()
        yield TourPanel(self._controller)


class TourApp(LabgridTuiApp):
    BINDINGS = [*LabgridTuiApp.BINDINGS, Binding("n", "tour_skip", "Skip step", show=True)]

    def __init__(self, speed: float = 1.0) -> None:
        self._controller = TourController()
        super().__init__(
            _tour_config(),
            coordinators=_tour_coordinators(),
            persist_coordinators=False,
            packs=[load_robot_pack()],
            pack_errors=[],
            ui_state=UiState(onboarded=True),
            persist_ui_state=False,
            fleet_source_factory=fleet_source_factory(speed),
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

    def _on_tour_hook(self, name: str, detail: str) -> None:
        if name == "cursor_changed":
            self._controller.on_cursor_changed()
        elif name == "marks_changed":
            self._controller.on_marks_changed()
        elif name == "detail_open":
            self._controller.on_detail_open()
        elif name == "detail_close":
            self._controller.on_detail_close()

    def _on_stream_event(self, event: Event) -> None:
        if (
            isinstance(event, PlaceChanged)
            and event.place.name == _WATCHED_PLACE
            and event.place.acquired
        ):
            self._controller.on_alice_acquire()
        super()._on_stream_event(event)

    def action_tour_skip(self) -> None:
        self._controller.skip()


def run_tour(speed: float = 1.0) -> None:
    TourApp(speed=speed).run()
