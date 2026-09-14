"""Tests for the `labgrid-tui config ...` and `labgrid-tui coordinator ...`
subcommands."""

import os
import tomllib
from pathlib import Path

import pytest

from labgrid_tui.__main__ import main
from labgrid_tui.config import load_config
from labgrid_tui.coordinators import default_coordinators_path, load_coordinators
from labgrid_tui.packs import default_packs_path, load_registry


def test_config_path_prints_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    assert main(["--config", str(config_path), "config", "path"]) == 0
    assert capsys.readouterr().out.strip() == str(config_path)


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--config", "{path}", "config", "show"], id="before-subcommand"),
        pytest.param(["config", "--config", "{path}", "show"], id="between-subcommands"),
        pytest.param(["config", "show", "--config", "{path}"], id="after-subcommand"),
    ],
)
def test_config_flag_recognized_at_every_subcommand_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
) -> None:
    """--config must resolve the same way regardless of where on the
    command line it appears: argparse only recognizes an option at the
    parser level its token belongs to, so each subcommand level needs its
    own copy of the flag (see _common_parser in __main__.py)."""
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.delenv("LG_COORDINATOR", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text('coordinator = "coord.lab:1234"\n')
    args = [arg.format(path=config_path) for arg in extra_args]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert f"config: {config_path} (ok)" in out
    assert "coordinator: coord.lab:1234 (from config)" in out


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["-x", "{addr}", "config", "show"], id="before-subcommand"),
        pytest.param(["config", "-x", "{addr}", "show"], id="between-subcommands"),
        pytest.param(["config", "show", "-x", "{addr}"], id="after-subcommand"),
    ],
)
def test_coordinator_flag_recognized_at_every_subcommand_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra_args: list[str],
) -> None:
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.delenv("LG_COORDINATOR", raising=False)
    config_path = tmp_path / "config.toml"
    # --config PATH pins the config file so this test only exercises where
    # -x lands; it is always given up front, ahead of the -x placement
    # under test.
    args = ["--config", str(config_path), *(arg.format(addr="flag.lab:9") for arg in extra_args)]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert "coordinator: flag.lab:9 (from flag)" in out


def test_flag_invalid_coordinator_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.delenv("LG_COORDINATOR", raising=False)
    config_path = tmp_path / "config.toml"
    rc = main(["--config", str(config_path), "-x", "host:notaport", "config", "show"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "invalid coordinator" in err


def test_env_invalid_coordinator_reported_and_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.setenv("LG_COORDINATOR", "host:notaport")
    config_path = tmp_path / "config.toml"
    config_path.write_text("")  # exists, so config_show reports the error detail
    rc = main(["--config", str(config_path), "config", "show"])
    assert rc == 1  # config_error set
    out = capsys.readouterr().out
    assert "coordinator: 127.0.0.1:20408 (from default)" in out
    assert "error:" in out
    assert "LG_COORDINATOR" in out


def test_config_show_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.delenv("LG_COORDINATOR", raising=False)
    config_path = tmp_path / "config.toml"
    assert main(["--config", str(config_path), "config", "show"]) == 0
    out = capsys.readouterr().out
    assert f"config: {config_path} (missing)" in out
    assert "coordinator: 127.0.0.1:20408 (from default)" in out
    assert "prefix: labgrid-client -x 127.0.0.1:20408 (default)" in out
    assert "capabilities: 0 custom" in out
    assert "commands: 0 custom" in out
    assert "proxy:" not in out


def test_config_show_ok_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.delenv("LG_COORDINATOR", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'coordinator = "coord.lab:1234"\n'
        'prefix = "mycli"\n'
        "[capabilities]\n"
        'MyResource = "power"\n'
        "[[commands]]\n"
        'category = "Custom"\n'
        'label = "Ping"\n'
        'suffix = "ssh -- ping"\n'
    )
    assert main(["--config", str(config_path), "config", "show"]) == 0
    out = capsys.readouterr().out
    assert f"config: {config_path} (ok)" in out
    assert "coordinator: coord.lab:1234 (from config)" in out
    assert "prefix: mycli (from config)" in out
    assert "capabilities: 1 custom" in out
    assert "commands: 1 custom" in out


def test_config_show_error_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.delenv("LG_COORDINATOR", raising=False)
    config_path = tmp_path / "config.toml"
    config_path.write_text("prefix = [unterminated\n")
    assert main(["--config", str(config_path), "config", "show"]) == 1
    out = capsys.readouterr().out
    assert f"config: {config_path} (error:" in out
    # The path must not be repeated inside the parenthetical detail.
    assert str(config_path) not in out.split("(error:", 1)[1]


def test_config_show_proxy_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LG_PROXY", "hop.lab")
    config_path = tmp_path / "config.toml"
    assert main(["--config", str(config_path), "config", "show"]) == 1
    out = capsys.readouterr().out
    assert "proxy: LG_PROXY set (unsupported)" in out


def test_config_init_creates_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "nested" / "config.toml"
    assert main(["--config", str(config_path), "config", "init"]) == 0
    assert config_path.exists()
    out = capsys.readouterr().out
    assert str(config_path) in out
    assert "created" in out


def test_config_init_refuses_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('prefix = "kept"\n')
    assert main(["--config", str(config_path), "config", "init"]) == 1
    out = capsys.readouterr().out
    assert str(config_path) in out
    assert "already exists" in out
    assert config_path.read_text() == 'prefix = "kept"\n'


def test_config_init_force_overwrites(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('prefix = "kept"\n')
    assert main(["--config", str(config_path), "config", "init", "--force"]) == 0
    assert config_path.read_text() != 'prefix = "kept"\n'


def test_config_init_template_parses_and_yields_defaults(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    assert main(["--config", str(config_path), "config", "init"]) == 0
    with config_path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data == {}
    config = load_config(None, {}, config_path)
    assert config.coordinator == "127.0.0.1:20408"
    assert config.coordinator_source == "default"
    assert config.prefix is None
    assert config.capability_overrides == {}
    assert config.command_templates == {}
    assert config.place_templates == ()
    assert config.config_error is None


def test_config_show_lists_coordinators_file_and_active_entry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    coordinators_path = default_coordinators_path(os.environ)
    assert main(["coordinator", "add", "lab", "lab.example:20408", "--use"]) == 0
    capsys.readouterr()
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert f"coordinators: {coordinators_path} (ok)" in out
    assert "active coordinator: lab (lab.example:20408)" in out


def test_config_show_no_coordinators_file(capsys: pytest.CaptureFixture[str]) -> None:
    coordinators_path = default_coordinators_path(os.environ)
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert f"coordinators: {coordinators_path} (missing)" in out
    assert "active coordinator: none" in out


# ---------------------------------------------------------------------
# `labgrid-tui coordinator ...`
# ---------------------------------------------------------------------


def test_coordinator_list_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "list"]) == 0
    assert "no coordinators configured" in capsys.readouterr().out


def test_coordinator_add_and_list(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "add", "home", "127.0.0.1:20408", "--use"]) == 0
    capsys.readouterr()

    assert main(["coordinator", "list"]) == 0
    out = capsys.readouterr().out
    assert "  lab  lab.example:20408" in out
    assert "* home  127.0.0.1:20408" in out


def test_coordinator_add_writes_prefix(tmp_path: Path) -> None:
    assert (
        main(
            [
                "coordinator",
                "add",
                "lab",
                "lab.example:20408",
                "--prefix",
                "mycli",
            ]
        )
        == 0
    )
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert coordinators.entries["lab"].prefix == "mycli"


def test_coordinator_add_invalid_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "bad name", "lab.example:20408"]) == 2
    assert "invalid coordinator name" in capsys.readouterr().err


def test_coordinator_add_invalid_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "no-port-here"]) == 2
    assert "host:port" in capsys.readouterr().err


def test_coordinator_add_duplicate_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "add", "lab", "other.lab:1"]) == 2
    assert "already exists" in capsys.readouterr().err


def test_coordinator_use_switches_current() -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    assert main(["coordinator", "add", "home", "127.0.0.1:20408"]) == 0
    assert main(["coordinator", "use", "home"]) == 0
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert coordinators.current == "home"


def test_coordinator_use_unknown_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "use", "nosuch"]) == 2
    err = capsys.readouterr().err
    assert "unknown coordinator" in err
    assert "lab" in err


def test_coordinator_remove() -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    assert main(["coordinator", "add", "home", "127.0.0.1:20408"]) == 0
    assert main(["coordinator", "remove", "lab"]) == 0
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert "lab" not in coordinators.entries
    assert "home" in coordinators.entries


def test_coordinator_remove_active_refuses_without_force(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408", "--use"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "remove", "lab"]) == 2
    assert "--force" in capsys.readouterr().err
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert "lab" in coordinators.entries
    assert coordinators.current == "lab"


def test_coordinator_remove_active_with_force_clears_current() -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408", "--use"]) == 0
    assert main(["coordinator", "remove", "lab", "--force"]) == 0
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert "lab" not in coordinators.entries
    assert coordinators.current is None


def test_coordinator_remove_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "remove", "nosuch"]) == 2
    assert "unknown coordinator" in capsys.readouterr().err


def test_coordinator_show_named(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "coordinator",
                "add",
                "lab",
                "lab.example:20408",
                "--prefix",
                "mycli",
                "--use",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["coordinator", "show", "lab"]) == 0
    out = capsys.readouterr().out
    assert "name: lab (active)" in out
    assert "address: lab.example:20408" in out
    assert "prefix: mycli" in out


def test_coordinator_show_default_prefix(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "show", "lab"]) == 0
    out = capsys.readouterr().out
    assert "prefix: labgrid-client -x lab.example:20408 (default)" in out


def test_coordinator_show_active_when_no_name_given(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408", "--use"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "show"]) == 0
    assert "name: lab (active)" in capsys.readouterr().out


def test_coordinator_show_no_active(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "show"]) == 1
    assert "no active coordinator" in capsys.readouterr().out


def test_coordinator_show_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "show", "nosuch"]) == 2
    assert "unknown coordinator" in capsys.readouterr().err


def test_coordinator_edit_address() -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    assert main(["coordinator", "edit", "lab", "--address", "new.lab:1"]) == 0
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert coordinators.entries["lab"].address == "new.lab:1"


def test_coordinator_edit_prefix() -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    assert main(["coordinator", "edit", "lab", "--prefix", "newcli"]) == 0
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert coordinators.entries["lab"].prefix == "newcli"


def test_coordinator_edit_no_prefix_clears_it() -> None:
    assert (
        main(
            [
                "coordinator",
                "add",
                "lab",
                "lab.example:20408",
                "--prefix",
                "mycli",
            ]
        )
        == 0
    )
    assert main(["coordinator", "edit", "lab", "--no-prefix"]) == 0
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert coordinators.entries["lab"].prefix is None


def test_coordinator_edit_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "edit", "nosuch", "--address", "x:1"]) == 2
    assert "unknown coordinator" in capsys.readouterr().err


def test_coordinator_edit_invalid_address(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["coordinator", "add", "lab", "lab.example:20408"]) == 0
    capsys.readouterr()
    assert main(["coordinator", "edit", "lab", "--address", "no-port"]) == 2
    assert "host:port" in capsys.readouterr().err
    coordinators = load_coordinators(default_coordinators_path(os.environ))
    assert coordinators.entries["lab"].address == "lab.example:20408"


def test_coordinator_edit_preserves_unknown_extra_keys(tmp_path: Path) -> None:
    """A shell's own per-coordinator fields must survive an edit of the
    address/prefix this CLI knows about."""
    from labgrid_tui.coordinators import CoordinatorEntry, Coordinators, save_coordinators

    coordinators_path = default_coordinators_path(os.environ)
    coords = Coordinators(
        entries={
            "lab": CoordinatorEntry(
                name="lab", address="lab.example:20408", extra={"auth_backend": "token"}
            )
        }
    )
    save_coordinators(coordinators_path, coords)

    assert main(["coordinator", "edit", "lab", "--address", "new.lab:1"]) == 0
    reloaded = load_coordinators(coordinators_path)
    assert reloaded.entries["lab"].address == "new.lab:1"
    assert reloaded.entries["lab"].extra == {"auth_backend": "token"}


# ---------------------------------------------------------------------
# `labgrid-tui pack ...`
# ---------------------------------------------------------------------

_VALID_PACK_TOML = """\
[pack]
name = "robot"
description = "Robot Framework recipes"

[[commands]]
label = "Smoke tests"
command = "robot -v PLACE:{place} tests/smoke"
"""

_NO_PACK_TABLE_TOML = "[[commands]]\nlabel = 'x'\ncommand = 'y'\n"


def _write_pack(tmp_path: Path, text: str = _VALID_PACK_TOML, name: str = "robot.toml") -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_pack_add_registers_path_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    out = capsys.readouterr().out
    assert "added pack robot (1 commands)" in out
    registry = load_registry(default_packs_path(os.environ))
    assert registry.packs["robot"].source == str(pack_file)
    assert registry.packs["robot"].cached is None


def test_pack_add_name_override(tmp_path: Path) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file), "--name", "robot-2"]) == 0
    registry = load_registry(default_packs_path(os.environ))
    assert "robot-2" in registry.packs
    assert "robot" not in registry.packs


def test_pack_add_invalid_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file), "--name", "bad name"]) == 2
    assert "invalid pack name" in capsys.readouterr().err


def test_pack_add_duplicate_name(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    capsys.readouterr()
    assert main(["pack", "add", str(pack_file)]) == 2
    assert "already exists" in capsys.readouterr().err


def test_pack_add_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pack", "add", str(tmp_path / "nosuch.toml")]) == 1
    assert "not found" in capsys.readouterr().err


def test_pack_add_missing_pack_table(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path, _NO_PACK_TABLE_TOML)
    assert main(["pack", "add", str(pack_file)]) == 1
    assert "[pack]" in capsys.readouterr().err


def test_pack_list_empty(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pack", "list"]) == 0
    assert "no command packs registered" in capsys.readouterr().out


def test_pack_list_after_add(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    capsys.readouterr()
    assert main(["pack", "list"]) == 0
    out = capsys.readouterr().out
    assert "robot" in out
    assert "1 commands" in out


def test_pack_show(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    capsys.readouterr()
    assert main(["pack", "show", "robot"]) == 0
    out = capsys.readouterr().out
    assert "name: robot" in out
    assert "Smoke tests" in out
    assert "robot -v PLACE:{place} tests/smoke" in out


def test_pack_show_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pack", "show", "nosuch"]) == 2
    assert "unknown pack" in capsys.readouterr().err


def test_pack_remove(tmp_path: Path) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    assert main(["pack", "remove", "robot"]) == 0
    registry = load_registry(default_packs_path(os.environ))
    assert "robot" not in registry.packs


def test_pack_remove_unknown(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pack", "remove", "nosuch"]) == 2
    assert "unknown pack" in capsys.readouterr().err


def test_pack_remove_never_deletes_a_path_packs_source_file(tmp_path: Path) -> None:
    """A path pack is read from its source in place on every start; the
    source file belongs to the user (a checkout, a hand-authored recipe),
    not to labgrid-tui, so `pack remove` must only ever drop the registry
    entry, never the file itself."""
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    assert main(["pack", "remove", "robot"]) == 0
    assert pack_file.exists()


def test_pack_remove_deletes_a_url_packs_cache_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A URL pack's cache file is labgrid-tui's own copy (fetched into
    ~/.config/labgrid-tui/packs/), so `pack remove` cleans it up: unlike
    a path pack's source, it is not the user's file."""

    class _FakeResponse:
        def __init__(self, data: bytes, *, final_url: str) -> None:
            self._data = data
            self._final_url = final_url

        def read(self, size: int = -1) -> bytes:
            return self._data if size is None or size < 0 else self._data[:size]

        def geturl(self) -> str:
            return self._final_url

        def __enter__(self) -> "_FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(_VALID_PACK_TOML.encode(), final_url=url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert main(["pack", "add", "https://git.example.org/raw/robot.toml"]) == 0

    registry = load_registry(default_packs_path(os.environ))
    cache_path = Path(registry.packs["robot"].cached or "")
    assert cache_path.exists()

    assert main(["pack", "remove", "robot"]) == 0
    assert not cache_path.exists()


def test_pack_update_no_url_packs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    capsys.readouterr()
    assert main(["pack", "update"]) == 0
    assert "no URL-sourced packs to update" in capsys.readouterr().out


def test_pack_update_unknown_name(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["pack", "update", "nosuch"]) == 2
    assert "unknown pack" in capsys.readouterr().err


def test_pack_update_fetches_url_pack(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from labgrid_tui.packs import PackRegistry, PackRegistryEntry, save_registry

    packs_path = default_packs_path(os.environ)
    save_registry(
        packs_path,
        PackRegistry(
            packs={
                "team": PackRegistryEntry(
                    name="team", source="https://git.example.org/raw/team.toml"
                )
            }
        ),
    )

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

    def fake_urlopen(url: str, timeout: float | None = None) -> _FakeResponse:
        return _FakeResponse(_VALID_PACK_TOML.replace("robot", "team", 1).encode(), final_url=url)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert main(["pack", "update"]) == 0
    out = capsys.readouterr().out
    assert "updated pack team (1 commands)" in out

    registry = load_registry(packs_path)
    entry = registry.packs["team"]
    assert entry.cached is not None
    assert Path(entry.cached).read_text().startswith("[pack]")


def test_config_show_lists_packs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pack_file = _write_pack(tmp_path)
    assert main(["pack", "add", str(pack_file)]) == 0
    capsys.readouterr()
    assert main(["config", "show"]) == 0
    out = capsys.readouterr().out
    assert "packs:" in out
    assert "robot: 1 commands (ok)" in out
