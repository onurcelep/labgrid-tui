"""Tests for evaluate_pack(): pack entries gated and rendered against a
real Place/Resource/Reservation, mirroring tests/model/test_commands.py."""

from labgrid_tui.coordinator.models import Place, Reservation, ReservationState, Resource
from labgrid_tui.model.commands import EntryState
from labgrid_tui.model.packs import Pack, PackCommandTemplate, evaluate_pack

ME = "laptop/alice"
OTHER = "host2/bob"
COORDINATOR = "coord.lab:20408"
PREFIX = "labgrid-client -x coord.lab:20408"


def _place(
    acquired: str | None = None,
    allowed: tuple[str, ...] = (),
    reservation: str | None = None,
    tags: dict[str, str] | None = None,
) -> Place:
    return Place(
        name="tb-1",
        aliases=(),
        comment="",
        tags=tags or {},
        matches=(),
        acquired=acquired,
        acquired_resources=(),
        allowed=allowed,
        created=0.0,
        changed=0.0,
        reservation=reservation,
    )


def _reservation(
    token: str, owner: str, state: ReservationState = ReservationState.allocated
) -> Reservation:
    return Reservation(
        owner=owner,
        token=token,
        state=state,
        prio=0.0,
        filters={},
        allocations={},
        created=0.0,
        timeout=0.0,
    )


def _res(cls: str, name: str = "r0", **params: object) -> Resource:
    return Resource(
        exporter="e",
        group="g",
        name=name,
        cls=cls,
        params=params,
        extra={},
        acquired="",
        avail=True,
    )


def _pack(*commands: PackCommandTemplate, name: str = "robot") -> Pack:
    return Pack(name=name, description="", source="test", commands=tuple(commands))


def test_category_is_pack_name_and_entries_are_copy_only() -> None:
    pack = _pack(PackCommandTemplate(label="Ping", command="ping {place}"), name="diag")
    entries = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert len(entries) == 1
    entry = entries[0]
    assert entry.template.category == "diag"
    assert entry.template.copy_only is True
    assert entry.state is EntryState.RUNNABLE
    assert entry.command_line == "ping tb-1"


def test_res_placeholder_resolves_from_matched_resource() -> None:
    pack = _pack(
        PackCommandTemplate(
            label="SSH",
            command="ssh {res.NetworkService.address}",
            requires=frozenset({"res.NetworkService"}),
        )
    )
    resources = [_res("NetworkService", address="10.0.0.5")]
    entries = evaluate_pack(pack, _place(), resources, ME, [], COORDINATOR, PREFIX)
    assert entries[0].state is EntryState.RUNNABLE
    assert entries[0].command_line == "ssh 10.0.0.5"


def test_res_placeholder_unresolved_without_matching_resource() -> None:
    pack = _pack(PackCommandTemplate(label="SSH", command="ssh {res.NetworkService.address}"))
    entries = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert entries[0].state is EntryState.UNAVAILABLE
    assert entries[0].reason == "needs NetworkService"
    # The raw, unrendered template stays visible so the user can see the
    # recipe even though it's greyed out.
    assert entries[0].command_line == "ssh {res.NetworkService.address}"


def test_requires_res_gates_independent_of_placeholder_use() -> None:
    pack = _pack(
        PackCommandTemplate(
            label="Full suite",
            command="robot tests/",
            requires=frozenset({"res.NetworkService"}),
        )
    )
    entries = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert entries[0].state is EntryState.UNAVAILABLE
    assert entries[0].reason == "needs NetworkService"


def test_requires_tag_gates() -> None:
    pack = _pack(
        PackCommandTemplate(label="X", command="echo x", requires=frozenset({"tag.board"}))
    )
    unmet = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert unmet[0].state is EntryState.UNAVAILABLE
    assert unmet[0].reason == "needs tag board"

    met = evaluate_pack(
        pack, _place(tags={"board": "generic-board"}), [], ME, [], COORDINATOR, PREFIX
    )
    assert met[0].state is EntryState.RUNNABLE


def test_requires_held_true_when_acquired_by_me() -> None:
    pack = _pack(PackCommandTemplate(label="X", command="echo x", requires=frozenset({"held"})))
    entries = evaluate_pack(pack, _place(acquired=ME), [], ME, [], COORDINATOR, PREFIX)
    assert entries[0].state is EntryState.RUNNABLE


def test_requires_held_true_when_allowed() -> None:
    pack = _pack(PackCommandTemplate(label="X", command="echo x", requires=frozenset({"held"})))
    entries = evaluate_pack(
        pack, _place(acquired=OTHER, allowed=(ME,)), [], ME, [], COORDINATOR, PREFIX
    )
    assert entries[0].state is EntryState.RUNNABLE


def test_requires_held_true_when_reservation_is_mine() -> None:
    pack = _pack(PackCommandTemplate(label="X", command="echo x", requires=frozenset({"held"})))
    reservations = [_reservation("tok-1", ME)]
    entries = evaluate_pack(
        pack, _place(reservation="tok-1"), [], ME, reservations, COORDINATOR, PREFIX
    )
    assert entries[0].state is EntryState.RUNNABLE


def test_requires_held_false_when_acquired_by_other() -> None:
    pack = _pack(PackCommandTemplate(label="X", command="echo x", requires=frozenset({"held"})))
    entries = evaluate_pack(pack, _place(acquired=OTHER), [], ME, [], COORDINATOR, PREFIX)
    assert entries[0].state is EntryState.UNAVAILABLE
    assert entries[0].reason == "hold the bench first"


def test_token_placeholder_resolves_to_my_reservation() -> None:
    pack = _pack(PackCommandTemplate(label="Wait", command="wait {token}"))
    reservations = [_reservation("tok-1", ME)]
    entries = evaluate_pack(
        pack, _place(reservation="tok-1"), [], ME, reservations, COORDINATOR, PREFIX
    )
    assert entries[0].state is EntryState.RUNNABLE
    assert entries[0].command_line == "wait tok-1"


def test_token_placeholder_unresolved_for_someone_elses_reservation() -> None:
    pack = _pack(PackCommandTemplate(label="Wait", command="wait {token}"))
    reservations = [_reservation("tok-1", OTHER)]
    entries = evaluate_pack(
        pack, _place(reservation="tok-1"), [], ME, reservations, COORDINATOR, PREFIX
    )
    assert entries[0].state is EntryState.UNAVAILABLE
    assert entries[0].reason == "needs reservation token"


def test_place_coordinator_prefix_placeholders() -> None:
    pack = _pack(PackCommandTemplate(label="X", command="{place} {coordinator} {prefix}"))
    entries = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert entries[0].command_line == f"tb-1 {COORDINATOR} {PREFIX}"


def test_malformed_template_is_unavailable_not_a_crash() -> None:
    """A pack file edited by hand after registration can break its own
    brace syntax; evaluate_pack() runs on every overlay open and palette
    keystroke, so it must degrade to UNAVAILABLE rather than raise."""
    pack = _pack(PackCommandTemplate(label="Broken", command="echo {place"))
    entries = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert entries[0].state is EntryState.UNAVAILABLE
    assert entries[0].reason == "invalid command template"


def test_multiple_commands_each_get_their_own_entry() -> None:
    pack = _pack(
        PackCommandTemplate(label="A", command="echo a"),
        PackCommandTemplate(label="B", command="echo b"),
    )
    entries = evaluate_pack(pack, _place(), [], ME, [], COORDINATOR, PREFIX)
    assert [e.template.label for e in entries] == ["A", "B"]
