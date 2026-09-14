"""In-memory fleet state, fed exclusively by stream events + reservation polls."""

from labgrid_tui.coordinator.match import resources_for_place
from labgrid_tui.coordinator.models import Place, Reservation, Resource, ResourceKey, resource_key
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    Event,
    PlaceChanged,
    PlaceDeleted,
    ResourceChanged,
    ResourceDeleted,
    RetryScheduled,
)


class FleetStore:
    def __init__(self) -> None:
        self.places: dict[str, Place] = {}
        self.resources: dict[ResourceKey, Resource] = {}
        self.reservations: list[Reservation] = []
        self.conn: ConnState = ConnState.CONNECTING

    def apply(self, event: Event) -> None:
        match event:
            case ConnectionChanged(state=state):
                self.conn = state
                if state is ConnState.CONNECTING:
                    # re-subscribe replays full state; clearing first handles
                    # deletions missed while disconnected
                    self.places.clear()
                    self.resources.clear()
            case PlaceChanged(place=place):
                self.places[place.name] = place
            case PlaceDeleted(name=name):
                self.places.pop(name, None)
            case ResourceChanged(resource=resource):
                self.resources[resource_key(resource)] = resource
            case ResourceDeleted(exporter=exporter, group=group, name=name):
                self.resources.pop((exporter, group, name), None)
            case RetryScheduled():
                pass  # display-only; the app forwards it to the overlay directly

    def resources_of(self, place: Place) -> list[Resource]:
        return resources_for_place(place, self.resources.values())
