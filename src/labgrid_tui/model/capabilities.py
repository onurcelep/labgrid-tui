"""Resource class -> capability tag mapping. Data, not code.

Class names are labgrid's registered exporter-side (Network*/Remote*)
resource classes. Unknown classes fall back to name-substring hints, then
None ("unknown" capability, still rendered).
"""

from collections.abc import Iterable

from labgrid_tui.coordinator.models import Resource

DEFAULT_CAPABILITIES: dict[str, str] = {
    # power
    "NetworkPowerPort": "power",
    "NetworkUSBPowerPort": "power",
    "NetworkYKUSHPowerPort": "power",
    "NetworkSiSPMPowerPort": "power",
    # console
    "NetworkSerialPort": "console",
    "NetworkSigrokUSBSerialDevice": "console",
    # digital io
    "NetworkSysfsGPIO": "io",
    "NetworkLXAIOBusPIO": "io",
    "NetworkHIDRelay": "io",
    "NetworkDeditecRelais8": "io",
    # sd mux
    "NetworkUSBSDMuxDevice": "sdmux",
    "NetworkUSBSDWireDevice": "sdmux",
    "NetworkUSBSDWire3Device": "sdmux",
    # usb mux
    "NetworkLXAUSBMux": "usbmux",
    # network access
    "NetworkService": "ssh",
    # media
    "NetworkUSBVideo": "video",
    "NetworkUSBAudioInput": "audio",
    # storage / flashing
    "NetworkUSBMassStorage": "storage",
    "NetworkUSBFlashableDevice": "flash",
    "NetworkDediprogFlasher": "flash",
    "NetworkFlashrom": "flash",
    "NetworkDFUDevice": "dfu",
    "NetworkAndroidFastboot": "fastboot",
    "RemoteAndroidUSBFastboot": "fastboot",
    "RemoteAndroidNetFastboot": "fastboot",
    "NetworkIMXUSBLoader": "bootstrap",
    "NetworkMXSUSBLoader": "bootstrap",
    "NetworkRKUSBLoader": "bootstrap",
    "NetworkAlteraUSBBlaster": "bootstrap",
    # instruments / debug
    "NetworkUSBTMC": "tmc",
    "NetworkUSBDebugger": "debug",
    "NetworkSigrokUSBDevice": "measure",
}

NAME_HINTS: tuple[tuple[str, str], ...] = (
    ("power", "power"),
    ("reset", "io"),
    ("console", "console"),
    ("serial", "console"),
)


def capability_of(resource: Resource, extra: dict[str, str] | None = None) -> str | None:
    merged = DEFAULT_CAPABILITIES if not extra else {**DEFAULT_CAPABILITIES, **extra}
    if resource.cls in merged:
        return merged[resource.cls]
    lowered = resource.name.lower()
    for substring, capability in NAME_HINTS:
        if substring in lowered:
            return capability
    return None


def capabilities_for(
    resources: Iterable[Resource], extra: dict[str, str] | None = None
) -> set[str]:
    return {cap for r in resources if (cap := capability_of(r, extra)) is not None}
