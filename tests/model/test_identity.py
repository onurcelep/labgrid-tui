import pytest

from labgrid_tui.coordinator.models import Place
from labgrid_tui.model.identity import (
    Access,
    access_reason,
    current_host,
    current_id,
    current_user,
    place_access,
)


def _place(acquired: str | None = None, allowed: tuple[str, ...] = ()) -> Place:
    return Place(name="tb-1", aliases=(), comment="", tags={}, matches=(),
                 acquired=acquired, acquired_resources=(), allowed=allowed,
                 created=0.0, changed=0.0, reservation=None)


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LG_USERNAME", "alice")
    monkeypatch.setenv("LG_HOSTNAME", "laptop")
    assert current_user() == "alice"
    assert current_host() == "laptop"
    assert current_id() == "laptop/alice"


def test_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LG_USERNAME", raising=False)
    monkeypatch.delenv("LG_HOSTNAME", raising=False)
    assert current_user()  # real getuser value, non-empty
    assert current_host()


def test_access_states() -> None:
    me = "laptop/alice"
    assert place_access(_place(acquired=None), me) is Access.NOT_ACQUIRED
    assert place_access(_place(acquired="laptop/alice"), me) is Access.USABLE
    assert (
        place_access(_place(acquired="host2/bob", allowed=("laptop/alice",)), me)
        is Access.USABLE
    )
    assert place_access(_place(acquired="host2/bob"), me) is Access.OTHER_USER
    assert place_access(_place(acquired="host2/alice"), me) is Access.OTHER_HOST


def test_reasons() -> None:
    me = "laptop/alice"
    assert access_reason(Access.USABLE, _place(acquired=me), me) is None
    assert "acquire" in str(access_reason(Access.NOT_ACQUIRED, _place(), me))
    other = access_reason(Access.OTHER_USER, _place(acquired="host2/bob"), me)
    assert "bob" in str(other) and "allow laptop/alice" in str(other)
    host = access_reason(Access.OTHER_HOST, _place(acquired="host2/alice"), me)
    assert "host2" in str(host)
