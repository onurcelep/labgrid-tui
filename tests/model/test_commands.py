from labgrid_tui.coordinator.models import Place, Reservation, ReservationState, Resource
from labgrid_tui.model.commands import (
    GLOBAL_TEMPLATES,
    PLACE_TEMPLATES,
    RESOURCE_TEMPLATES,
    VERB_ACQUIRE,
    CommandTemplate,
    EntryState,
    default_prefix,
    evaluate,
    get_command_line,
    is_verb,
    render_global,
    reservation_entries,
)

ME = "laptop/alice"
OTHER = "host2/bob"
PREFIX = default_prefix("coord.lab:20408")


def _place(
    acquired: str | None = None,
    allowed: tuple[str, ...] = (),
    reservation: str | None = None,
) -> Place:
    return Place(
        name="tb-1",
        aliases=(),
        comment="",
        tags={},
        matches=(),
        acquired=acquired,
        acquired_resources=(),
        allowed=allowed,
        created=0.0,
        changed=0.0,
        reservation=reservation,
    )


def _reservation(
    token: str,
    owner: str,
    state: ReservationState,
    place: str | None = None,
) -> Reservation:
    return Reservation(
        owner=owner,
        token=token,
        state=state,
        prio=0.0,
        filters={},
        allocations={"main": place} if place else {},
        created=0.0,
        timeout=0.0,
    )


def _res(cls: str, avail: bool = True) -> Resource:
    return Resource(
        exporter="e", group="g", name="r0", cls=cls, params={}, extra={}, acquired="", avail=avail
    )


def test_prefix_rendering() -> None:
    assert PREFIX == "labgrid-client -x coord.lab:20408"
    entries = evaluate(_place(), [], ME, PREFIX)
    get = next(e for e in entries if is_verb(e.template, VERB_ACQUIRE))
    assert get.template.label == "Acquire"
    assert get.command_line == get_command_line(PREFIX, "tb-1")
    assert get.command_line == (
        "labgrid-client -x coord.lab:20408 -p +$(labgrid-client -x coord.lab:20408 "
        "reserve --wait --shell name=tb-1 | cut -d= -f2) acquire"
    )
    assert get.template.copy_only  # shell syntax: always copied, never executed
    plain = next(e for e in entries if e.template.label == "Acquire now (no queue)")
    assert plain.command_line == "labgrid-client -x coord.lab:20408 -p tb-1 acquire"


def test_get_verb_follows_the_place_state() -> None:
    free = next(e for e in evaluate(_place(), [], ME, PREFIX) if is_verb(e.template, VERB_ACQUIRE))
    assert (free.template.label, free.state) == ("Acquire", EntryState.RUNNABLE)

    taken = evaluate(_place(acquired="host2/bob"), [], ME, PREFIX)
    get = next(e for e in taken if is_verb(e.template, VERB_ACQUIRE))
    assert (get.template.label, get.state) == ("Queue and acquire", EntryState.RUNNABLE)
    plain = next(e for e in taken if e.template.label == "Acquire now (no queue)")
    assert plain.state is EntryState.UNAVAILABLE
    release = next(e for e in taken if e.template.label == "Release")
    assert (release.state, release.reason) == (EntryState.UNAVAILABLE, "held by bob")

    own = evaluate(_place(acquired=ME), [], ME, PREFIX)
    mine = next(e for e in own if is_verb(e.template, VERB_ACQUIRE))
    assert (mine.state, mine.reason) == (EntryState.UNAVAILABLE, "already yours")


def test_usable_by_me_covers_allowed() -> None:
    place = _place(acquired="host2/bob", allowed=(ME,))
    entries = evaluate(place, [], ME, PREFIX)
    release = next(e for e in entries if e.template.label == "Release")
    assert release.state is EntryState.RUNNABLE


def test_resource_templates_hidden_without_resource() -> None:
    entries = evaluate(_place(acquired=ME), [], ME, PREFIX)
    assert not any(e.template.category == "Power" for e in entries)


def test_resource_online_precondition() -> None:
    place = _place(acquired=ME)
    online = evaluate(place, [_res("NetworkPowerPort")], ME, PREFIX)
    power_on = next(e for e in online if e.template.label == "Power on")
    assert power_on.state is EntryState.RUNNABLE

    offline = evaluate(place, [_res("NetworkPowerPort", avail=False)], ME, PREFIX)
    power_on = next(e for e in offline if e.template.label == "Power on")
    assert power_on.state is EntryState.UNAVAILABLE
    assert "offline" in str(power_on.reason)


def test_needs_args_templates_render_placeholders() -> None:
    entries = evaluate(_place(acquired=ME), [], ME, PREFIX)
    allow = next(e for e in entries if e.template.label == "Allow user")
    assert allow.template.needs_args
    assert "<host>/<user>" in allow.command_line


def test_plugin_templates_merge() -> None:
    extra = {"FutureResource": (CommandTemplate("Magic", "Do magic", "magic go"),)}
    entries = evaluate(
        _place(acquired=ME), [_res("FutureResource")], ME, PREFIX, extra_templates=extra
    )
    assert any(e.template.label == "Do magic" for e in entries)


def test_global_templates_render_without_place() -> None:
    reserve = next(t for t in GLOBAL_TEMPLATES if t.label == "Reserve")
    line = render_global(reserve, PREFIX)
    assert line.startswith("labgrid-client -x coord.lab:20408 reserve")
    assert "-p" not in line


def test_console_is_interactive() -> None:
    console = RESOURCE_TEMPLATES["NetworkSerialPort"][0]
    assert console.interactive and console.label == "Serial console"


def test_place_templates_exist() -> None:
    labels = {t.label for t in PLACE_TEMPLATES}
    assert {"Acquire", "Release", "Allow user", "Device info", "Export env"} <= labels


def _named_res(name: str, cls: str, avail: bool = True) -> Resource:
    return Resource(
        exporter="e", group="g", name=name, cls=cls, params={}, extra={}, acquired="", avail=avail
    )


def test_multiple_resources_get_per_resource_entries() -> None:
    place = _place(acquired=ME)
    resources = [
        _named_res("dut-network", "NetworkService"),
        _named_res("gateway-network", "NetworkService", avail=False),
    ]
    entries = evaluate(place, resources, ME, PREFIX)
    ssh = [e for e in entries if e.template.label.startswith("SSH (")]
    assert [e.template.label for e in ssh] == ["SSH (dut-network)", "SSH (gateway-network)"]
    assert ssh[0].command_line.endswith("ssh -n dut-network")
    assert ssh[0].state is EntryState.RUNNABLE
    assert ssh[1].state is EntryState.UNAVAILABLE  # that resource is offline
    assert "offline" in str(ssh[1].reason)


def test_single_resource_stays_clean() -> None:
    place = _place(acquired=ME)
    entries = evaluate(place, [_named_res("serial", "NetworkSerialPort")], ME, PREFIX)
    console = next(e for e in entries if e.template.category == "Connect")
    assert console.template.label == "Serial console"
    assert console.command_line.endswith("-p tb-1 console")


def test_positional_name_style_for_console_and_io() -> None:
    place = _place(acquired=ME)
    resources = [
        _named_res("dut-console", "NetworkSerialPort"),
        _named_res("gw-console", "NetworkSerialPort"),
    ]
    entries = evaluate(place, resources, ME, PREFIX)
    consoles = [e for e in entries if e.template.category == "Connect"]
    assert consoles[0].command_line.endswith("console dut-console")

    ios = evaluate(
        place,
        [_named_res("r1", "NetworkSysfsGPIO"), _named_res("r2", "NetworkSysfsGPIO")],
        ME,
        PREFIX,
    )
    high = [e for e in ios if e.template.label.startswith("I/O set high")]
    assert high[0].command_line.endswith("io high r1")
    assert not high[0].template.needs_args  # concrete name: directly runnable


def test_io_single_resource_runnable_without_name() -> None:
    place = _place(acquired=ME)
    entries = evaluate(place, [_named_res("relay", "NetworkSysfsGPIO")], ME, PREFIX)
    high = next(e for e in entries if e.template.label == "I/O set high")
    assert high.command_line.endswith("io high")
    assert high.state is EntryState.RUNNABLE


def test_extra_place_templates() -> None:
    extra = (CommandTemplate("Custom", "My verb", "monitor"),)
    entries = evaluate(_place(), [], ME, PREFIX, extra_place=extra)
    custom = next(e for e in entries if e.template.label == "My verb")
    assert custom.command_line.endswith("-p tb-1 monitor")


def test_free_place_lists_resource_commands_greyed() -> None:
    entries = evaluate(_place(), [_res("NetworkPowerPort")], ME, PREFIX)
    power_on = next(e for e in entries if e.template.label == "Power on")
    assert (power_on.state, power_on.reason) == (EntryState.UNAVAILABLE, "hold the bench first")
    assert [e.template.label for e in entries if e.template.category == "Manage"] == [
        "Acquire",
        "Acquire now (no queue)",
        "Release",
        "Allow user",
        "Reserve (queue)",
    ]


def test_resource_commands_say_who_holds_or_reserved_the_place() -> None:
    held = evaluate(_place(acquired="host2/bob"), [_res("NetworkPowerPort")], ME, PREFIX)
    power_on = next(e for e in held if e.template.label == "Power on")
    assert (power_on.state, power_on.reason) == (EntryState.UNAVAILABLE, "held by bob")

    reservations = [_reservation("TOK", "host9/carol", ReservationState.allocated, place="tb-1")]
    reserved = evaluate(
        _place(reservation="TOK"), [_res("NetworkPowerPort")], ME, PREFIX, reservations=reservations
    )
    power_on = next(e for e in reserved if e.template.label == "Power on")
    assert (power_on.state, power_on.reason) == (EntryState.UNAVAILABLE, "reserved by carol")


def test_held_place_shows_resource_commands_and_manage() -> None:
    entries = evaluate(_place(acquired="host2/bob"), [_res("NetworkPowerPort")], ME, PREFIX)
    labels = [e.template.label for e in entries]
    assert "Power on" in labels and "Release" in labels and "Allow user" in labels
    assert "Queue and acquire" in labels
    release = next(e for e in entries if e.template.label == "Release")
    assert release.state is EntryState.UNAVAILABLE  # held by someone else


def test_reserved_place_counts_as_held() -> None:
    place = Place(
        name="tb-1",
        aliases=(),
        comment="",
        tags={},
        matches=(),
        acquired=None,
        acquired_resources=(),
        allowed=(),
        created=0.0,
        changed=0.0,
        reservation="TOK",
    )
    entries = evaluate(place, [_res("NetworkPowerPort")], ME, PREFIX)
    assert any(e.template.label == "Power on" for e in entries)


def test_acquire_refused_when_all_resources_offline() -> None:
    entries = evaluate(_place(), [_res("NetworkPowerPort", avail=False)], ME, PREFIX)
    for label in ("Acquire", "Acquire now (no queue)"):
        acquire = next(e for e in entries if e.template.label == label)
        assert acquire.state is EntryState.UNAVAILABLE
        assert acquire.reason == "all resources offline"


def test_acquire_runnable_when_some_resources_online() -> None:
    resources = [
        _res("NetworkPowerPort", avail=False),
        _named_res("dut-network", "NetworkService", avail=True),
    ]
    entries = evaluate(_place(), resources, ME, PREFIX)
    acquire = next(e for e in entries if e.template.label == "Acquire")
    assert acquire.state is EntryState.RUNNABLE


def test_acquire_runnable_when_no_resources_matched() -> None:
    # No resources matched at all is a different (unknown) state from every
    # matched resource being offline; the guard must not block it.
    entries = evaluate(_place(), [], ME, PREFIX)
    acquire = next(e for e in entries if e.template.label == "Acquire")
    assert acquire.state is EntryState.RUNNABLE


def test_info_group_always_present() -> None:
    for place in (_place(), _place(acquired=ME)):
        labels = {e.template.label for e in evaluate(place, [], ME, PREFIX)}
        assert {"Device info", "Export env"} <= labels


# ----------------------------------------------------------------------
# Reserve/Acquire gating, mirrored against the coordinator's AcquirePlace:
# free & unreserved; acquired/reserved by other; reserved by me (waiting);
# reserved by me (allocated).
# ----------------------------------------------------------------------


def test_gating_free_and_unreserved() -> None:
    entries = evaluate(_place(), [], ME, PREFIX)
    acquire = next(e for e in entries if e.template.label == "Acquire")
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert acquire.state is EntryState.RUNNABLE
    assert reserve.state is EntryState.UNAVAILABLE
    assert reserve.reason == "free: acquire directly"


def test_gating_acquired_by_other() -> None:
    entries = evaluate(_place(acquired=OTHER), [], ME, PREFIX)
    labels = [e.template.label for e in entries]
    assert "Acquire" not in labels  # unchanged: hidden outright, not shown as unavailable
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert reserve.state is EntryState.RUNNABLE


def test_gating_reserved_by_other_not_yet_acquired() -> None:
    place = _place(reservation="TOK")
    reservations = [_reservation("TOK", OTHER, ReservationState.waiting)]
    entries = evaluate(place, [], ME, PREFIX, reservations=reservations)
    acquire = next(e for e in entries if e.template.label == "Acquire now (no queue)")
    get = next(e for e in entries if is_verb(e.template, VERB_ACQUIRE))
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert acquire.state is EntryState.UNAVAILABLE
    assert acquire.reason == f"reserved by {OTHER}"
    assert (get.template.label, get.state) == ("Queue and acquire", EntryState.RUNNABLE)
    assert reserve.state is EntryState.RUNNABLE


def test_gating_reserved_by_me_waiting() -> None:
    place = _place(reservation="TOK")
    reservations = [_reservation("TOK", ME, ReservationState.waiting)]
    entries = evaluate(place, [], ME, PREFIX, reservations=reservations)
    acquire = next(e for e in entries if e.template.label == "Acquire now (no queue)")
    get = next(e for e in entries if is_verb(e.template, VERB_ACQUIRE))
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert acquire.state is EntryState.UNAVAILABLE
    assert acquire.reason == "waiting for allocation"
    assert get.template.label == "Acquire (your reservation is waiting)"
    assert get.state is EntryState.RUNNABLE  # the one-liner waits for the allocation
    assert reserve.state is EntryState.UNAVAILABLE
    assert reserve.reason == "reserved by you"

    cancel = next(e for e in entries if e.template.label == "Cancel reservation TOK")
    assert cancel.state is EntryState.RUNNABLE
    acquire_token = next(e for e in entries if e.template.label == "Acquire allocated place +TOK")
    assert acquire_token.state is EntryState.UNAVAILABLE
    assert acquire_token.reason == "waiting"


def test_gating_reserved_by_me_allocated() -> None:
    place = _place(reservation="TOK")
    reservations = [_reservation("TOK", ME, ReservationState.allocated, place="tb-1")]
    entries = evaluate(place, [], ME, PREFIX, reservations=reservations)
    acquire = next(e for e in entries if e.template.label == "Acquire now (no queue)")
    get = next(e for e in entries if is_verb(e.template, VERB_ACQUIRE))
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert acquire.state is EntryState.RUNNABLE
    assert acquire.reason is None
    assert acquire.command_line == f"{PREFIX} -p tb-1 acquire"  # plain acquire, no +TOKEN
    assert (get.template.label, get.state) == ("Acquire", EntryState.RUNNABLE)
    assert reserve.state is EntryState.UNAVAILABLE
    assert reserve.reason == "reserved by you"

    cancel = next(e for e in entries if e.template.label == "Cancel reservation TOK")
    assert cancel.state is EntryState.RUNNABLE
    assert cancel.command_line == f"{PREFIX} cancel-reservation TOK"

    acquire_token = next(e for e in entries if e.template.label == "Acquire allocated place +TOK")
    assert acquire_token.state is EntryState.RUNNABLE
    assert acquire_token.command_line == f"{PREFIX} -p +TOK acquire"


def test_gating_acquired_by_me_reserve_reason() -> None:
    entries = evaluate(_place(acquired=ME), [], ME, PREFIX)
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert reserve.state is EntryState.UNAVAILABLE
    assert reserve.reason == "already yours"


def test_reserve_command_line_has_no_place_flag() -> None:
    """`reserve` takes `name=<PLACE>` as a filter, not `-p PLACE`."""
    entries = evaluate(_place(), [], ME, PREFIX)
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert reserve.command_line == f"{PREFIX} reserve name=tb-1"


def test_gating_allowed_but_not_mine_reserve_reason() -> None:
    # Someone else acquired the place and ran "allow" for me: usable, but
    # not mine, so the reason must not claim ownership.
    place = _place(acquired="host/other", allowed=(ME,))
    entries = evaluate(place, [], ME, PREFIX, reservations=[])
    reserve = next(e for e in entries if e.template.label == "Reserve (queue)")
    assert reserve.state is EntryState.UNAVAILABLE
    assert reserve.reason == "usable via allow"


def test_reservations_group_absent_without_my_reservations() -> None:
    entries = evaluate(_place(), [], ME, PREFIX, reservations=[])
    assert not any(e.template.category == "Reservations" for e in entries)


def test_reservations_group_filters_to_owner() -> None:
    reservations = [
        _reservation("MINE", ME, ReservationState.waiting),
        _reservation("THEIRS", OTHER, ReservationState.waiting),
    ]
    entries = reservation_entries(reservations, ME, PREFIX)
    labels = {e.template.label for e in entries}
    assert any("MINE" in label for label in labels)
    assert not any("THEIRS" in label for label in labels)


def test_reservation_entries_independent_of_place() -> None:
    """Reservation-level entries show up for any place's evaluate() call,
    not just the place the reservation is allocated to."""
    reservations = [_reservation("TOK", ME, ReservationState.allocated, place="tb-99")]
    other_place = Place(
        name="tb-1",
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
    entries = evaluate(other_place, [], ME, PREFIX, reservations=reservations)
    assert any(e.template.category == "Reservations" for e in entries)
