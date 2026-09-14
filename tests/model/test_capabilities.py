from labgrid_tui.coordinator.models import Resource
from labgrid_tui.model.capabilities import capabilities_for, capability_of


def _res(cls: str, name: str = "r0") -> Resource:
    return Resource(
        exporter="e", group="g", name=name, cls=cls, params={}, extra={}, acquired="", avail=True
    )


def test_known_classes() -> None:
    assert capability_of(_res("NetworkPowerPort")) == "power"
    assert capability_of(_res("NetworkSerialPort")) == "console"
    assert capability_of(_res("NetworkUSBSDMuxDevice")) == "sdmux"
    assert capability_of(_res("NetworkService")) == "ssh"
    assert capability_of(_res("NetworkUSBVideo")) == "video"
    assert capability_of(_res("NetworkSysfsGPIO")) == "io"


def test_unknown_class_is_none_not_error() -> None:
    assert capability_of(_res("FutureResource")) is None


def test_name_hint_for_unknown_class() -> None:
    assert capability_of(_res("FutureResource", name="dut-power-relay")) == "power"


def test_extra_map_overrides() -> None:
    assert capability_of(_res("FutureResource"), extra={"FutureResource": "magic"}) == "magic"
    # extra also overrides built-ins
    assert capability_of(_res("NetworkSerialPort"), extra={"NetworkSerialPort": "uart"}) == "uart"


def test_capabilities_for_aggregates() -> None:
    caps = capabilities_for([_res("NetworkPowerPort"), _res("NetworkSerialPort"), _res("Nope")])
    assert caps == {"power", "console"}
