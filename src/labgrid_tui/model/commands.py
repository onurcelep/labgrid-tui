"""Command templates and the validity engine.

Every entry is a real labgrid-client command line. Templates with
needs_args=True contain <placeholders> and are copy-only. Resource templates
are keyed by resource class; a class with no resource matched to the place
produces no entries, and no resource commands appear at all until the place
is held (acquired or reserved).
"""

import enum
from dataclasses import dataclass, field, replace

from labgrid_tui.coordinator.models import Place, Reservation, ReservationState, Resource
from labgrid_tui.model.identity import Access, access_reason, place_access

USABLE = frozenset({"usable-by-me"})
FREE = frozenset({"free"})
ONLINE = frozenset({"resource-online"})
USABLE_ONLINE = USABLE | ONLINE
# A place's matched resources must not be entirely offline: acquiring a
# dead exporter is refused rather than dispatched.
ANY_ONLINE = frozenset({"any-online"})
# Reserve (queue) gating: the inverse of acquiring, runnable exactly when
# the place is already spoken for by someone else.
QUEUE = frozenset({"queue"})


@dataclass(frozen=True)
class CommandTemplate:
    category: str
    label: str
    cli_suffix: str
    interactive: bool = False
    needs_args: bool = False
    requires: frozenset[str] = field(default_factory=frozenset)
    # How labgrid-client selects a specific resource for this command when
    # a place has several of the class: "flag" appends -n NAME, and
    # "positional" appends NAME (console and io take it positionally).
    name_style: str = "none"
    # Pack entries only: forces CliActionRunner.run to copy instead of
    # execute, no matter what a pack file's own entry looks like: the
    # TUI never shells out to a command it did not define itself.
    copy_only: bool = False


class EntryState(enum.Enum):
    RUNNABLE = enum.auto()
    UNAVAILABLE = enum.auto()


@dataclass(frozen=True)
class CommandEntry:
    template: CommandTemplate
    command_line: str
    state: EntryState
    reason: str | None


# Group (tab) order. Reservations follows Manage: it holds the user's
# own reservation-level entries (independent of the cursor place), not
# place-level ones.
GROUP_ORDER: tuple[str, ...] = (
    "Connect",
    "File Transfer",
    "Flash",
    "Power",
    "Hardware",
    "Media",
    "Manage",
    "Reservations",
    "Info",
)

# Manage: what you can do to the place itself. "Reserve (queue)" is always
# offered alongside Acquire/Release: its own requires computes whether
# queueing makes sense (see _reserve_reason), so both buckets carry it.
_RESERVE_TEMPLATE = CommandTemplate(
    "Manage", "Reserve (queue)", "reserve name=<PLACE>", requires=QUEUE
)
MANAGE_HELD: tuple[CommandTemplate, ...] = (
    CommandTemplate(
        "Manage", "Allow user", "allow <host>/<user>", needs_args=True, requires=USABLE
    ),
    CommandTemplate("Manage", "Release", "release", requires=USABLE),
    _RESERVE_TEMPLATE,
)
MANAGE_FREE: tuple[CommandTemplate, ...] = (
    CommandTemplate("Manage", "Acquire", "acquire", requires=FREE | ANY_ONLINE),
    _RESERVE_TEMPLATE,
)
INFO_TEMPLATES: tuple[CommandTemplate, ...] = (
    CommandTemplate("Info", "Device info", "show"),
    CommandTemplate("Info", "Export env", "env"),
)
# Kept for callers that want "every place-level template" regardless of
# state (tests, docs); evaluate() picks the state-appropriate subset.
PLACE_TEMPLATES: tuple[CommandTemplate, ...] = MANAGE_FREE + MANAGE_HELD + INFO_TEMPLATES


def _t(
    category: str,
    label: str,
    suffix: str,
    *,
    interactive: bool = False,
    needs_args: bool = False,
    name_style: str = "flag",
) -> CommandTemplate:
    return CommandTemplate(
        category,
        label,
        suffix,
        interactive=interactive,
        needs_args=needs_args,
        requires=USABLE_ONLINE,
        name_style=name_style,
    )


_POWER = (
    _t("Power", "Power on", "power on"),
    _t("Power", "Power off", "power off"),
    _t("Power", "Power cycle", "power cycle"),
    _t("Power", "Power state", "power get"),
)
_IO = (
    _t("Hardware", "I/O set high", "io high", name_style="positional"),
    _t("Hardware", "I/O set low", "io low", name_style="positional"),
    _t("Hardware", "I/O get state", "io get", name_style="positional"),
)
_SDMUX = (
    _t("Hardware", "SD-Mux switching", "sd-mux <dut|host|off|client|get>", needs_args=True),
    _t("Flash", "Write image to mass storage", "write-image <image-path>", needs_args=True),
)
_VIDEO = (_t("Media", "Video streaming", "video", interactive=True),)
_DFU = (_t("Flash", "DFU device communication", "dfu <download|detach|list>", needs_args=True),)
_FASTBOOT = (_t("Flash", "Fastboot commands", "fastboot <args>", needs_args=True),)
_BOOTSTRAP = (_t("Flash", "Start bootloader", "bootstrap <filename>", needs_args=True),)

RESOURCE_TEMPLATES: dict[str, tuple[CommandTemplate, ...]] = {
    # power
    "NetworkPowerPort": _POWER,
    "NetworkUSBPowerPort": _POWER,
    "NetworkSiSPMPowerPort": _POWER,
    "NetworkYKUSHPowerPort": _POWER,
    "PDUDaemonPort": _POWER,
    "TasmotaPowerPort": _POWER,
    # GPIO can drive power and digital I/O
    "NetworkSysfsGPIO": _POWER + _IO,
    # digital I/O
    "NetworkDeditecRelais8": _IO,
    "NetworkHIDRelay": _IO,
    "NetworkLXAIOBusPIO": _IO,
    "ModbusTCPCoil": _IO,
    "OneWirePIO": _IO,
    "HttpDigitalOutput": _IO,
    "WaveshareModbusTCPCoil": _IO,
    # console
    "NetworkSerialPort": (
        _t("Connect", "Serial console", "console", interactive=True, name_style="positional"),
        _t(
            "Connect",
            "Serial console (loop)",
            "console --loop",
            interactive=True,
            name_style="positional",
        ),
    ),
    # network
    "NetworkService": (
        _t("Connect", "SSH", "ssh", interactive=True),
        _t("Connect", "Telnet connection", "telnet", interactive=True, name_style="none"),
        _t("Connect", "SSH port forwarding", "forward -L <local>:<remote>", needs_args=True),
        _t(
            "File Transfer",
            "Copy files via SCP",
            "scp <local-path> <remote-path>",
            needs_args=True,
        ),
        _t(
            "File Transfer",
            "Sync files via rsync",
            "rsync <local-path> <remote-path>",
            needs_args=True,
        ),
        _t(
            "File Transfer",
            "Mount via SSHFS",
            "sshfs <remote-path> <mount-point>",
            interactive=True,
            needs_args=True,
        ),
    ),
    # sd mux
    "NetworkUSBSDMuxDevice": _SDMUX,
    "NetworkUSBSDWireDevice": _SDMUX,
    "NetworkUSBSDWire3Device": _SDMUX,
    "NetworkUSBMassStorage": (
        _t("Flash", "Write image to mass storage", "write-image <image-path>", needs_args=True),
    ),
    # usb mux
    "NetworkLXAUSBMux": (_t("Hardware", "USB muxer control", "usb-mux <links>", needs_args=True),),
    # media
    "NetworkUSBVideo": _VIDEO,
    "HTTPVideoStream": _VIDEO,
    "NetworkUSBAudioInput": (_t("Media", "Audio streaming", "audio", interactive=True),),
    # flashing
    "NetworkDFUDevice": _DFU,
    "DFUDevice": _DFU,
    "RemoteAndroidUSBFastboot": _FASTBOOT,
    "RemoteAndroidNetFastboot": _FASTBOOT,
    "NetworkAndroidFastboot": _FASTBOOT,
    "AndroidUSBFastboot": _FASTBOOT,
    "AndroidNetFastboot": _FASTBOOT,
    "NetworkIMXUSBLoader": _BOOTSTRAP,
    "NetworkMXSUSBLoader": _BOOTSTRAP,
    "NetworkRKUSBLoader": _BOOTSTRAP,
    "NetworkAlteraUSBBlaster": _BOOTSTRAP,
}

GLOBAL_TEMPLATES: tuple[CommandTemplate, ...] = (
    CommandTemplate("Reservation", "Reserve", "reserve <key>=<value>", needs_args=True),
    CommandTemplate("Reservation", "Wait for reservation", "wait <token>", needs_args=True),
    CommandTemplate(
        "Reservation", "Cancel reservation", "cancel-reservation <token>", needs_args=True
    ),
)


def default_prefix(coordinator: str) -> str:
    return f"labgrid-client -x {coordinator}"


def render_global(template: CommandTemplate, prefix: str) -> str:
    return f"{prefix} {template.cli_suffix}"


def find_reservation(
    token: str, reservations: list[Reservation] | None
) -> Reservation | None:
    for reservation in reservations or ():
        if reservation.token == token:
            return reservation
    return None


def _acquire_reason(
    place: Place, me: str, reservations: list[Reservation] | None
) -> str | None:
    """Mirrors the coordinator's AcquirePlace: refused outright once the
    place is acquired; refused with PERMISSION_DENIED when a reservation
    exists whose owner is not the caller; the owner's own acquire is
    otherwise allowed once allocated, but blocked while still waiting."""
    if place.acquired:
        return f"already acquired by {place.acquired}"
    if not place.reservation:
        return None
    reservation = find_reservation(place.reservation, reservations)
    if reservation is None:
        # Token unknown to the caller (reservations not supplied, or a
        # race with the reservation poll): nothing to block on.
        return None
    if reservation.owner != me:
        return f"reserved by {reservation.owner}"
    if reservation.state is ReservationState.waiting:
        return "waiting for allocation"
    return None


def _reserve_reason(
    place: Place, me: str, reservations: list[Reservation] | None
) -> str | None:
    """Queueing only makes sense for a place someone else already holds or
    has reserved; the coordinator's `reserve` filters for any free place
    anyway, so a free one should acquire directly instead of queueing."""
    if place.acquired == me:
        return "already yours"
    if me in place.allowed:
        return "usable via allow"
    if not place.acquired and not place.reservation:
        return "free: acquire directly"
    if place.reservation:
        reservation = find_reservation(place.reservation, reservations)
        if reservation is not None and reservation.owner == me:
            return "reserved by you"
    return None


def _entry(
    template: CommandTemplate,
    place: Place,
    access: Access,
    me: str,
    prefix: str,
    resource_online: bool | None,
    all_resources_offline: bool = False,
    reservations: list[Reservation] | None = None,
) -> CommandEntry:
    if "queue" in template.requires:
        # `reserve` takes a filter, not `-p PLACE`: labgrid-client resolves
        # the place from `name=<PLACE>` among the reserve filters, not from
        # a place-selector flag.
        command_line = f"{prefix} reserve name={place.name}"
    else:
        command_line = f"{prefix} -p {place.name} {template.cli_suffix}"
    reason: str | None = None
    if "free" in template.requires:
        reason = _acquire_reason(place, me, reservations)
        if reason is None and "any-online" in template.requires and all_resources_offline:
            reason = "all resources offline"
    elif "queue" in template.requires:
        reason = _reserve_reason(place, me, reservations)
    elif "any-online" in template.requires and all_resources_offline:
        reason = "all resources offline"
    elif "usable-by-me" in template.requires and access is not Access.USABLE:
        reason = access_reason(access, place, me)
    elif "resource-online" in template.requires and resource_online is False:
        reason = "resource offline"
    state = EntryState.RUNNABLE if reason is None else EntryState.UNAVAILABLE
    return CommandEntry(template, command_line, state, reason)


def reservation_entries(
    reservations: list[Reservation] | None, me: str, prefix: str
) -> list[CommandEntry]:
    """Reservation-level entries for reservations owned by *me*: cancel it,
    or acquire the place it was allocated (the `+TOKEN` place alias, which
    the coordinator resolves via `Place.reservation`). Independent of any
    particular place, unlike every other entry `evaluate()` produces."""
    entries: list[CommandEntry] = []
    for reservation in reservations or ():
        if reservation.owner != me:
            continue
        token = reservation.token
        cancel_template = CommandTemplate(
            "Reservations", f"Cancel reservation {token}", f"cancel-reservation {token}"
        )
        entries.append(
            CommandEntry(
                cancel_template,
                f"{prefix} cancel-reservation {token}",
                EntryState.RUNNABLE,
                None,
            )
        )
        acquire_template = CommandTemplate(
            "Reservations", f"Acquire allocated place +{token}", f"-p +{token} acquire"
        )
        command_line = f"{prefix} -p +{token} acquire"
        if reservation.state is ReservationState.allocated:
            entries.append(
                CommandEntry(acquire_template, command_line, EntryState.RUNNABLE, None)
            )
        else:
            entries.append(
                CommandEntry(acquire_template, command_line, EntryState.UNAVAILABLE, "waiting")
            )
    return entries


def _for_resource(template: CommandTemplate, resource_name: str) -> CommandTemplate:
    """Specialize a template to one named resource of its class."""
    if template.name_style == "flag":
        suffix = f"{template.cli_suffix} -n {resource_name}"
    elif template.name_style == "positional":
        suffix = f"{template.cli_suffix} {resource_name}"
    else:
        return template
    return replace(
        template,
        label=f"{template.label} ({resource_name})",
        cli_suffix=suffix,
    )


def evaluate(
    place: Place,
    resources: list[Resource],
    me: str,
    prefix: str,
    extra_templates: dict[str, tuple[CommandTemplate, ...]] | None = None,
    extra_place: tuple[CommandTemplate, ...] | None = None,
    reservations: list[Reservation] | None = None,
) -> list[CommandEntry]:
    access = place_access(place, me)
    # A place someone holds (acquired) or has queued for (reserved) exposes
    # its operational commands; a free place only offers to be acquired.
    held = bool(place.acquired) or bool(place.reservation)
    # Acquire is only ever offered while nobody holds the place outright;
    # Reserve is offered either way (both buckets carry it, see
    # _RESERVE_TEMPLATE) and computes its own runnability from place state.
    manage = MANAGE_HELD if place.acquired else MANAGE_FREE
    place_templates = manage + INFO_TEMPLATES + tuple(extra_place or ())
    all_resources_offline = bool(resources) and not any(r.avail for r in resources)
    entries = [
        _entry(t, place, access, me, prefix, None, all_resources_offline, reservations)
        for t in place_templates
    ]
    # Reservation-level entries (cancel / acquire-once-allocated) are
    # independent of the cursor place: they belong to *me*, not to `place`.
    entries.extend(reservation_entries(reservations, me, prefix))
    if not held:
        return entries

    by_cls: dict[str, list[Resource]] = {}
    for resource in resources:
        by_cls.setdefault(resource.cls, []).append(resource)

    templates = dict(RESOURCE_TEMPLATES)
    if extra_templates:
        for cls_name, extra in extra_templates.items():
            templates[cls_name] = templates.get(cls_name, ()) + tuple(extra)

    for cls_name, cls_templates in templates.items():
        matched = by_cls.get(cls_name)
        if not matched:
            continue  # hidden: resource not present on this place
        online = any(r.avail for r in matched)
        for template in cls_templates:
            if len(matched) > 1 and template.name_style != "none":
                # Several resources of this class: one entry per resource,
                # with the resource name baked into the command so the CLI
                # cannot silently pick the wrong one, and validity tracking
                # that specific resource's availability.
                entries.extend(
                    _entry(_for_resource(template, r.name), place, access, me, prefix, r.avail)
                    for r in matched
                )
            else:
                entries.append(_entry(template, place, access, me, prefix, online))
    return entries
