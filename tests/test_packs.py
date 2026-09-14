"""Tests for pack file parsing, name validation, placeholder resolution,
URL fetching, and the packs.toml registry (parse/validate/round-trip; see
tests/model/test_packs.py for evaluate_pack() through fake places)."""

import tomllib
from pathlib import Path

import pytest

from labgrid_tui.model.packs import PlaceholderContext, render_command
from labgrid_tui.packs import (
    FETCH_TIMEOUT,
    MAX_PACK_BYTES,
    PackError,
    PackRegistry,
    PackRegistryEntry,
    cache_pack,
    default_pack_cache_dir,
    default_packs_path,
    fetch_url,
    is_url,
    load_registered_packs,
    load_registry,
    parse_pack_toml,
    save_registry,
    validate_pack_name,
)

# ---------------------------------------------------------------------
# Pack file parsing
# ---------------------------------------------------------------------

_VALID_PACK = """\
[pack]
name = "robot"
description = "Robot Framework recipes"

[[commands]]
label = "Smoke tests"
command = "robot -v PLACE:{place} tests/smoke"
requires = ["res.NetworkService"]
"""


def test_parse_valid_pack() -> None:
    pack, errors = parse_pack_toml(tomllib.loads(_VALID_PACK), "robot.toml")
    assert errors == []
    assert pack is not None
    assert pack.name == "robot"
    assert pack.description == "Robot Framework recipes"
    assert len(pack.commands) == 1
    cmd = pack.commands[0]
    assert cmd.label == "Smoke tests"
    assert cmd.command == "robot -v PLACE:{place} tests/smoke"
    assert cmd.requires == frozenset({"res.NetworkService"})


def test_parse_missing_pack_table_fails_whole_file() -> None:
    pack, errors = parse_pack_toml(tomllib.loads("[[commands]]\nlabel='x'\ncommand='y'\n"), "s")
    assert pack is None
    assert errors


def test_parse_invalid_pack_name_fails_whole_file() -> None:
    data = tomllib.loads('[pack]\nname = "bad name!"\n')
    pack, errors = parse_pack_toml(data, "s")
    assert pack is None
    assert "invalid pack name" in errors[0]


def test_parse_missing_name_fails_whole_file() -> None:
    data = tomllib.loads('[pack]\ndescription = "no name"\n')
    pack, errors = parse_pack_toml(data, "s")
    assert pack is None


def test_parse_skips_malformed_command_entries_but_keeps_the_rest() -> None:
    data = tomllib.loads("""\
[pack]
name = "mixed"

[[commands]]
label = "Good"
command = "echo hi"

[[commands]]
label = "Missing command"

[[commands]]
command = "echo no-label"

[[commands]]
label = "Bad requires"
command = "echo bad"
requires = ["not-a-real-requirement"]
""")
    pack, errors = parse_pack_toml(data, "s")
    assert pack is not None
    assert len(pack.commands) == 1
    assert pack.commands[0].label == "Good"
    assert len(errors) == 3


@pytest.mark.parametrize("command", ["echo {place", "echo place}", "echo {", "echo }"])
def test_parse_rejects_malformed_command_template(command: str) -> None:
    """A malformed template is caught at load time, the same way a
    missing label or a bad requires item is: skipped and counted, not
    left to crash render_command() later."""
    data = tomllib.loads(
        f'[pack]\nname = "x"\n[[commands]]\nlabel = "Broken"\ncommand = "{command}"\n'
    )
    pack, errors = parse_pack_toml(data, "s")
    assert pack is not None
    assert len(pack.commands) == 0
    assert any("invalid command template" in e for e in errors)


def test_parse_keeps_escaped_braces() -> None:
    data = tomllib.loads(
        '[pack]\nname = "x"\n[[commands]]\nlabel = "OK"\ncommand = "echo {{literal}}"\n'
    )
    pack, errors = parse_pack_toml(data, "s")
    assert errors == []
    assert pack is not None
    assert len(pack.commands) == 1


@pytest.mark.parametrize("label", ["Acquire", "acquire", "ACQUIRE", "Release", "release"])
def test_parse_rejects_label_colliding_with_builtin_verb(label: str) -> None:
    """DashboardScreen._dispatch_verb matches place-verb keybindings by
    label; a pack entry named "Acquire"/"Release" would be indistinguishable
    from the real thing, so it is refused at load time."""
    data = tomllib.loads(
        f'[pack]\nname = "x"\n[[commands]]\nlabel = "{label}"\ncommand = "echo hi"\n'
    )
    pack, errors = parse_pack_toml(data, "s")
    assert pack is not None
    assert len(pack.commands) == 0
    assert any("collides" in e for e in errors)


def test_parse_requires_accepts_res_tag_held() -> None:
    data = tomllib.loads("""\
[pack]
name = "gate"

[[commands]]
label = "A"
command = "echo a"
requires = ["res.NetworkService", "tag.env", "held"]
""")
    pack, errors = parse_pack_toml(data, "s")
    assert errors == []
    assert pack is not None
    assert pack.commands[0].requires == frozenset({"res.NetworkService", "tag.env", "held"})


def test_parse_not_a_table() -> None:
    pack, errors = parse_pack_toml(["not", "a", "table"], "s")
    assert pack is None
    assert errors


# ---------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------


def test_validate_pack_name_accepts_charset() -> None:
    assert validate_pack_name("robot-team_01.v2") == "robot-team_01.v2"


@pytest.mark.parametrize("bad", ["", "  ", "bad name", "bad/name", "bad:name", "..", "."])
def test_validate_pack_name_rejects(bad: str) -> None:
    with pytest.raises(PackError):
        validate_pack_name(bad)


def test_cache_pack_path_confined_to_cache_dir(tmp_path: Path) -> None:
    """The cache filename is built from a name that has already passed
    validate_pack_name (no '/', never '.' or '..' alone), so cache_pack
    can only ever write inside cache_dir: never above it."""
    cache_dir = tmp_path / "packs"
    path = cache_pack(cache_dir, validate_pack_name("robot"), b"data")
    assert Path(path).parent.resolve() == cache_dir.resolve()


# ---------------------------------------------------------------------
# Placeholder resolution
# ---------------------------------------------------------------------


def _ctx(**overrides: object) -> PlaceholderContext:
    base = dict(
        place="tb-1",
        coordinator="coord.lab:20408",
        prefix="labgrid-client -x coord.lab:20408",
        token=None,
        tags={},
        resources_by_class={},
    )
    base.update(overrides)
    return PlaceholderContext(**base)  # type: ignore[arg-type]


def test_render_resolves_place_coordinator_prefix() -> None:
    rendered, reason = render_command("{place}@{coordinator} via {prefix}", _ctx())
    assert reason is None
    assert rendered == "tb-1@coord.lab:20408 via labgrid-client -x coord.lab:20408"


def test_render_token_missing_is_unavailable() -> None:
    rendered, reason = render_command("wait {token}", _ctx())
    assert rendered is None
    assert reason == "needs reservation token"


def test_render_token_present() -> None:
    rendered, reason = render_command("wait {token}", _ctx(token="tok-1"))
    assert reason is None
    assert rendered == "wait tok-1"


def test_render_tag_missing() -> None:
    rendered, reason = render_command("board={tag.board}", _ctx())
    assert rendered is None
    assert reason == "needs tag board"


def test_render_tag_present() -> None:
    rendered, reason = render_command("board={tag.board}", _ctx(tags={"board": "generic-board"}))
    assert reason is None
    assert rendered == "board=generic-board"


def test_render_unknown_placeholder_never_raises() -> None:
    rendered, reason = render_command("{totally.unknown.thing}", _ctx())
    assert rendered is None
    assert reason is not None
    assert "unknown placeholder" in reason


def test_render_literal_braces_untouched() -> None:
    rendered, reason = render_command("echo {{literal}}", _ctx())
    assert reason is None
    assert rendered == "echo {literal}"


@pytest.mark.parametrize(
    "command",
    ["echo {place", "echo place}", "{", "}"],
    ids=["unclosed", "unopened", "lone-open", "lone-close"],
)
def test_render_malformed_template_is_unavailable_not_a_raise(command: str) -> None:
    """render_command() runs on every overlay open and palette keystroke;
    a template a file-edit broke after registration must degrade to an
    UNAVAILABLE reason, never raise ValueError out of string.Formatter."""
    rendered, reason = render_command(command, _ctx())
    assert rendered is None
    assert reason == "invalid command template"


# ---------------------------------------------------------------------
# URL fetching (mocked urlopen)
# ---------------------------------------------------------------------


def test_is_url_https_only() -> None:
    assert is_url("https://git.example.org/robot.toml") is True
    assert is_url("http://git.example.org/robot.toml") is False
    assert is_url("/home/me/robot.toml") is False
    assert is_url("file:///home/me/robot.toml") is False


def test_fetch_url_rejects_non_https() -> None:
    with pytest.raises(PackError, match="https"):
        fetch_url("http://example.org/robot.toml")


class _FakeResponse:
    def __init__(self, data: bytes, *, final_url: str) -> None:
        self._data = data
        self._final_url = final_url

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._data
        return self._data[:size]

    def geturl(self) -> str:
        return self._final_url

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def test_fetch_url_returns_body(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float | None]] = []

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        calls.append((url, timeout))
        return _FakeResponse(b"[pack]\nname='x'\n", final_url=url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    body = fetch_url("https://git.example.org/robot.toml")
    assert body == b"[pack]\nname='x'\n"
    assert calls == [("https://git.example.org/robot.toml", FETCH_TIMEOUT)]


def test_fetch_url_wraps_urlerror(monkeypatch: pytest.MonkeyPatch) -> None:
    import urllib.error

    def fake_urlopen(url: str, timeout: float | None = None) -> None:
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(PackError, match="no route to host"):
        fetch_url("https://git.example.org/robot.toml")


def test_fetch_url_rejects_http_after_redirect(monkeypatch: pytest.MonkeyPatch) -> None:
    """urlopen follows a redirect wherever it points; an https source that
    302s to plain http must still be refused, not just the original URL."""

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(b"[pack]\nname='x'\n", final_url="http://evil.example.org/robot.toml")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(PackError, match="non-https"):
        fetch_url("https://git.example.org/robot.toml")


def test_fetch_url_rejects_oversized_response(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = b"x" * (MAX_PACK_BYTES + 1)

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(oversized, final_url=url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    with pytest.raises(PackError, match=str(MAX_PACK_BYTES)):
        fetch_url("https://git.example.org/robot.toml")


def test_fetch_url_accepts_response_at_exactly_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    at_cap = b"x" * MAX_PACK_BYTES

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(at_cap, final_url=url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert fetch_url("https://git.example.org/robot.toml") == at_cap


# ---------------------------------------------------------------------
# Registry: round-trip, unknown-key preservation, path helpers
# ---------------------------------------------------------------------


def test_missing_registry_loads_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.toml") == PackRegistry()


def test_load_registered_packs_uses_registry_name_not_file_name(tmp_path: Path) -> None:
    """`pack add --name` may register a pack under a name that differs
    from its file's own [pack].name; the registry name must win so the
    command overlay tab and `pack remove`/`pack show` agree on what the
    pack is called."""
    pack_file = tmp_path / "robot.toml"
    pack_file.write_text('[pack]\nname = "robot"\n[[commands]]\nlabel="x"\ncommand="y"\n')
    registry = PackRegistry(
        packs={"my-robot": PackRegistryEntry(name="my-robot", source=str(pack_file))}
    )
    loaded = load_registered_packs(registry)
    assert len(loaded) == 1
    assert loaded[0].pack is not None
    assert loaded[0].pack.name == "my-robot"


def test_registry_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "packs.toml"
    registry = PackRegistry(
        packs={
            "robot": PackRegistryEntry(name="robot", source="/home/me/robot.toml"),
            "team": PackRegistryEntry(
                name="team",
                source="https://git.example.org/raw/team.toml",
                cached="/home/me/.config/labgrid-tui/packs/team.toml",
            ),
        }
    )
    save_registry(path, registry)
    loaded = load_registry(path)
    assert loaded.packs["robot"] == PackRegistryEntry(
        name="robot", source="/home/me/robot.toml", cached=None, extra={}
    )
    assert loaded.packs["team"].cached == "/home/me/.config/labgrid-tui/packs/team.toml"


def test_registry_preserves_unknown_extra_keys(tmp_path: Path) -> None:
    path = tmp_path / "packs.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('[[packs]]\nname = "robot"\nsource = "/x/robot.toml"\nauthor = "qa-team"\n')
    registry = load_registry(path)
    assert registry.packs["robot"].extra == {"author": "qa-team"}
    save_registry(path, registry)
    reloaded = load_registry(path)
    assert reloaded.packs["robot"].extra == {"author": "qa-team"}


def test_registry_preserves_insertion_order_across_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "packs.toml"
    registry = PackRegistry()
    registry.packs["b"] = PackRegistryEntry(name="b", source="/b.toml")
    registry.packs["a"] = PackRegistryEntry(name="a", source="/a.toml")
    save_registry(path, registry)
    loaded = load_registry(path)
    assert list(loaded.packs) == ["b", "a"]


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "packs.toml"
    save_registry(path, PackRegistry())
    assert path.exists()


def test_default_packs_path_honours_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    env = {"XDG_CONFIG_HOME": "/tmp/xdg"}
    assert default_packs_path(env) == Path("/tmp/xdg/labgrid-tui/packs.toml")
    assert default_pack_cache_dir(env) == Path("/tmp/xdg/labgrid-tui/packs")
