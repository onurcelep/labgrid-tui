from labgrid_tui.coordinator.models import Place, Resource
from labgrid_tui.coordinator.stream import (
    ConnectionChanged,
    ConnState,
    PlaceChanged,
    PlaceDeleted,
    ResourceChanged,
    ResourceDeleted,
)
from labgrid_tui.ui.store import FleetStore


def _place(name: str = "tb-1") -> Place:
    return Place(
        name=name,
        aliases=(),
        comment="",
        tags={},
        matches=(),
        acquired=None,
        acquired_resources=(),
        allowed=(),
        created=0.0,
        changed=0.0,
        reservation=None,
    )


def _res(name: str = "r0") -> Resource:
    return Resource(
        exporter="e",
        group="g",
        name=name,
        cls="NetworkSerialPort",
        params={},
        extra={},
        acquired="",
        avail=True,
    )


def test_apply_place_lifecycle() -> None:
    store = FleetStore()
    store.apply(PlaceChanged(_place()))
    assert "tb-1" in store.places
    store.apply(PlaceDeleted("tb-1"))
    assert "tb-1" not in store.places


def test_apply_resource_lifecycle() -> None:
    store = FleetStore()
    store.apply(ResourceChanged(_res()))
    assert ("e", "g", "r0") in store.resources
    store.apply(ResourceDeleted("e", "g", "r0"))
    assert ("e", "g", "r0") not in store.resources


def test_connecting_clears_state() -> None:
    store = FleetStore()
    store.apply(PlaceChanged(_place()))
    store.apply(ResourceChanged(_res()))
    store.apply(ConnectionChanged(ConnState.CONNECTING))
    assert store.places == {}
    assert store.resources == {}
    assert store.conn is ConnState.CONNECTING
