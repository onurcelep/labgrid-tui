from labgrid_tui.coordinator.match import pattern_matches, resources_for_place
from labgrid_tui.coordinator.models import Place, Resource, ResourceMatchPattern


def _res(
    exporter: str = "exp1", group: str = "g1", name: str = "serial", cls: str = "NetworkSerialPort"
) -> Resource:
    return Resource(
        exporter=exporter,
        group=group,
        name=name,
        cls=cls,
        params={},
        extra={},
        acquired="",
        avail=True,
    )


def _place(*matches: ResourceMatchPattern) -> Place:
    return Place(
        name="tb-1",
        aliases=(),
        comment="",
        tags={},
        matches=tuple(matches),
        acquired=None,
        acquired_resources=(),
        allowed=(),
        created=0.0,
        changed=0.0,
        reservation=None,
    )


def test_exact_and_wildcard_components() -> None:
    assert pattern_matches(ResourceMatchPattern("exp1", "g1", "NetworkSerialPort"), _res())
    assert pattern_matches(ResourceMatchPattern("*", "g1", "*"), _res())
    assert pattern_matches(ResourceMatchPattern("exp*", "*", "Network*"), _res())
    assert not pattern_matches(ResourceMatchPattern("exp2", "*", "*"), _res())
    assert not pattern_matches(ResourceMatchPattern("*", "*", "NetworkPowerPort"), _res())


def test_name_component_checked_only_when_both_present() -> None:
    named = ResourceMatchPattern("*", "*", "*", name="serial")
    assert pattern_matches(named, _res(name="serial"))
    assert not pattern_matches(named, _res(name="console"))
    # pattern without name ignores resource name
    assert pattern_matches(ResourceMatchPattern("*", "*", "*"), _res(name="anything"))
    # empty resource name skips the name check (upstream semantics)
    assert pattern_matches(named, _res(name=""))


def test_rename_is_ignored_for_matching() -> None:
    p = ResourceMatchPattern("*", "*", "*", name="serial", rename="console0")
    assert pattern_matches(p, _res(name="serial"))


def test_resources_for_place() -> None:
    place = _place(ResourceMatchPattern("exp1", "g1", "*"))
    mine = _res()
    other = _res(exporter="exp2")
    assert resources_for_place(place, [mine, other]) == [mine]


def test_place_with_no_matches_gets_nothing() -> None:
    assert resources_for_place(_place(), [_res()]) == []
