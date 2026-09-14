"""Tests for the coordinators.toml storage model and its integration into
config resolution (-x/LG_COORDINATOR/current/config.toml precedence,
name lookup, and unknown-name handling)."""

from pathlib import Path

import pytest

from labgrid_tui.config import load_config
from labgrid_tui.coordinators import (
    CoordinatorEntry,
    CoordinatorError,
    Coordinators,
    default_coordinators_path,
    load_coordinators,
    save_coordinators,
    validate_coordinator,
    validate_name,
)

# ---------------------------------------------------------------------
# Storage: round-trip, atomic write, unknown-key preservation
# ---------------------------------------------------------------------


def test_missing_file_loads_empty(tmp_path: Path) -> None:
    coords = load_coordinators(tmp_path / "nope.toml")
    assert coords == Coordinators(current=None, entries={})


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "coordinators.toml"
    coords = Coordinators(
        current="lab",
        entries={
            "lab": CoordinatorEntry(name="lab", address="lab.example:20408", prefix="mycli"),
            "home": CoordinatorEntry(name="home", address="127.0.0.1:20408"),
        },
    )
    save_coordinators(path, coords)
    loaded = load_coordinators(path)
    assert loaded.current == "lab"
    assert loaded.entries["lab"] == CoordinatorEntry(
        name="lab", address="lab.example:20408", prefix="mycli", extra={}
    )
    assert loaded.entries["home"] == CoordinatorEntry(
        name="home", address="127.0.0.1:20408", prefix=None, extra={}
    )


def test_save_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "coordinators.toml"
    save_coordinators(path, Coordinators())
    assert path.exists()


def test_save_is_atomic_no_leftover_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "coordinators.toml"
    save_coordinators(
        path,
        Coordinators(
            current="a",
            entries={
                "a": CoordinatorEntry(name="a", address="a.lab:1"),
            },
        ),
    )
    leftovers = list(tmp_path.glob(".coordinators-*"))
    assert leftovers == []
    assert path.exists()


def test_unknown_keys_round_trip_through_edit_of_another_entry(tmp_path: Path) -> None:
    """A shell built on labgrid-tui may store extra per-coordinator keys
    (auth, ssh, ...); editing a *different* entry must leave them intact."""
    path = tmp_path / "coordinators.toml"
    path.write_text(
        'current = "lab"\n'
        "[coordinators.lab]\n"
        'address = "lab.example:20408"\n'
        'auth_backend = "token"\n'
        "[coordinators.lab.ssh]\n"
        'ssh_user = "root"\n'
        "[coordinators.home]\n"
        'address = "127.0.0.1:20408"\n'
    )
    coords = load_coordinators(path)
    assert coords.entries["lab"].extra["auth_backend"] == "token"
    assert coords.entries["lab"].extra["ssh"] == {"ssh_user": "root"}

    # Edit "home" only, then save: "lab"'s extras must survive untouched.
    coords.entries["home"].prefix = "labgrid-client -x 127.0.0.1:20408"
    save_coordinators(path, coords)

    reloaded = load_coordinators(path)
    assert reloaded.entries["lab"].extra["auth_backend"] == "token"
    assert reloaded.entries["lab"].extra["ssh"] == {"ssh_user": "root"}
    assert reloaded.entries["lab"].address == "lab.example:20408"
    assert reloaded.entries["home"].prefix == "labgrid-client -x 127.0.0.1:20408"


def test_unknown_keys_survive_create_and_delete_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "coordinators.toml"
    path.write_text('[coordinators.lab]\naddress = "lab.example:20408"\ncustom_field = "keepme"\n')
    coords = load_coordinators(path)
    # Create a new entry alongside "lab".
    coords.entries["home"] = CoordinatorEntry(name="home", address="127.0.0.1:20408")
    save_coordinators(path, coords)
    coords = load_coordinators(path)
    assert coords.entries["lab"].extra == {"custom_field": "keepme"}
    # Delete "home" again.
    del coords.entries["home"]
    save_coordinators(path, coords)
    coords = load_coordinators(path)
    assert coords.entries["lab"].extra == {"custom_field": "keepme"}
    assert "home" not in coords.entries


def test_malformed_file_raises_coordinator_error(tmp_path: Path) -> None:
    path = tmp_path / "coordinators.toml"
    path.write_text("current = [unterminated\n")
    with pytest.raises(CoordinatorError):
        load_coordinators(path)


def test_entry_without_address_is_skipped(tmp_path: Path) -> None:
    path = tmp_path / "coordinators.toml"
    path.write_text('[coordinators.broken]\nprefix = "x"\n')
    coords = load_coordinators(path)
    assert coords.entries == {}


def test_default_coordinators_path_xdg() -> None:
    path = default_coordinators_path({"XDG_CONFIG_HOME": "/tmp/xdg"})
    assert path == Path("/tmp/xdg/labgrid-tui/coordinators.toml")


# ---------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------


def test_validate_name_accepts_allowed_charset() -> None:
    assert validate_name("lab-01.dev_1") == "lab-01.dev_1"


@pytest.mark.parametrize("name", ["", "  ", "has space", "bad/slash", "bad:colon"])
def test_validate_name_rejects_bad_names(name: str) -> None:
    with pytest.raises(CoordinatorError):
        validate_name(name)


def test_validate_coordinator_accepts_host_port() -> None:
    assert validate_coordinator("lab.example:20408") == "lab.example:20408"


def test_validate_coordinator_requires_port() -> None:
    with pytest.raises(CoordinatorError):
        validate_coordinator("lab.example")


def test_validate_coordinator_ipv6_via_rsplit() -> None:
    assert validate_coordinator("::1:20408") == "::1:20408"
    assert validate_coordinator("[::1]:20408") == "[::1]:20408"


@pytest.mark.parametrize("address", ["", "host:", "host:0", "host:70000", "host:abc"])
def test_validate_coordinator_rejects_bad_addresses(address: str) -> None:
    with pytest.raises(CoordinatorError):
        validate_coordinator(address)


# ---------------------------------------------------------------------
# Resolution: precedence, name lookup, unknown-name behaviour
# ---------------------------------------------------------------------


def _write_coordinators(path: Path, current: str | None, **entries: str) -> None:
    coords = Coordinators(
        current=current,
        entries={
            name: CoordinatorEntry(name=name, address=address) for name, address in entries.items()
        },
    )
    save_coordinators(path, coords)


def test_flag_name_resolves_to_entry(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, None, lab="lab.example:20408")
    config = load_config("lab", {}, tmp_path / "config.toml", coordinators_path=coordinators_path)
    assert config.coordinator == "lab.example:20408"
    assert config.coordinator_source == "coordinator lab"


def test_flag_literal_address_still_works_with_entries_present(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, None, lab="lab.example:20408")
    config = load_config(
        "other.lab:1234", {}, tmp_path / "config.toml", coordinators_path=coordinators_path
    )
    assert config.coordinator == "other.lab:1234"
    assert config.coordinator_source == "flag"


def test_flag_bare_word_not_a_name_is_a_hostname(tmp_path: Path) -> None:
    # Named coordinators never change what an unrelated bare host means.
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, None, lab="lab.example:20408")
    config = load_config(
        "nosuch", {}, tmp_path / "config.toml", coordinators_path=coordinators_path
    )
    assert config.coordinator == "nosuch:20408"
    assert config.coordinator_source == "flag"


def test_flag_bare_word_without_coordinators_file_behaves_as_before(tmp_path: Path) -> None:
    # No coordinators.toml at all: identical to pre-feature behaviour.
    config = load_config(
        "myhost",
        {},
        tmp_path / "config.toml",
        coordinators_path=tmp_path / "coordinators.toml",
    )
    assert config.coordinator == "myhost:20408"
    assert config.coordinator_source == "flag"


def test_env_wins_over_current(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, "lab", lab="lab.example:20408")
    config = load_config(
        None,
        {"LG_COORDINATOR": "env.lab:9"},
        tmp_path / "config.toml",
        coordinators_path=coordinators_path,
    )
    assert config.coordinator == "env.lab:9"
    assert config.coordinator_source == "env"


def test_current_wins_over_config_coordinator(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, "lab", lab="lab.example:20408")
    config_path = tmp_path / "config.toml"
    config_path.write_text('coordinator = "other.lab:1234"\n')
    config = load_config(None, {}, config_path, coordinators_path=coordinators_path)
    assert config.coordinator == "lab.example:20408"
    assert config.coordinator_source == "coordinator lab"


def test_current_dangling_reference_falls_through(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    save_coordinators(coordinators_path, Coordinators(current="ghost", entries={}))
    config_path = tmp_path / "config.toml"
    config_path.write_text('coordinator = "other.lab:1234"\n')
    config = load_config(None, {}, config_path, coordinators_path=coordinators_path)
    assert config.coordinator == "other.lab:1234"
    assert config.coordinator_source == "config"


def test_config_coordinator_name_resolves_to_entry(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, None, lab="lab.example:20408")
    config_path = tmp_path / "config.toml"
    config_path.write_text('coordinator = "lab"\n')
    config = load_config(None, {}, config_path, coordinators_path=coordinators_path)
    assert config.coordinator == "lab.example:20408"
    assert config.coordinator_source == "coordinator lab"


def test_config_coordinator_bare_word_not_a_name_is_a_hostname(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    _write_coordinators(coordinators_path, None, lab="lab.example:20408")
    config_path = tmp_path / "config.toml"
    config_path.write_text('coordinator = "nosuch"\n')
    config = load_config(None, {}, config_path, coordinators_path=coordinators_path)
    assert config.coordinator == "nosuch:20408"
    assert config.coordinator_source == "config"
    assert config.config_error is None


def test_no_coordinators_file_no_behaviour_change(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('coordinator = "other.lab"\n')
    config = load_config(None, {}, config_path, coordinators_path=tmp_path / "coordinators.toml")
    assert config.coordinator == "other.lab:20408"
    assert config.coordinator_source == "config"
    assert config.config_error is None


def test_entry_prefix_used_when_config_prefix_unset(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    save_coordinators(
        coordinators_path,
        Coordinators(
            current="lab",
            entries={
                "lab": CoordinatorEntry(
                    name="lab",
                    address="lab.example:20408",
                    prefix="labgrid-client -x lab.example:20408 --extra",
                )
            },
        ),
    )
    config = load_config(None, {}, tmp_path / "config.toml", coordinators_path=coordinators_path)
    assert config.prefix is None
    assert config.coordinator_prefix == "labgrid-client -x lab.example:20408 --extra"


def test_config_toml_prefix_wins_over_entry_prefix(tmp_path: Path) -> None:
    coordinators_path = tmp_path / "coordinators.toml"
    save_coordinators(
        coordinators_path,
        Coordinators(
            current="lab",
            entries={
                "lab": CoordinatorEntry(
                    name="lab", address="lab.example:20408", prefix="entry-prefix"
                )
            },
        ),
    )
    config_path = tmp_path / "config.toml"
    config_path.write_text('prefix = "top-level-prefix"\n')
    config = load_config(None, {}, config_path, coordinators_path=coordinators_path)
    assert config.prefix == "top-level-prefix"
    assert config.coordinator_prefix == "entry-prefix"


def test_default_coordinators_path_defaults_when_absent(tmp_path: Path) -> None:
    """load_config with no coordinators_path falls back to
    default_coordinators_path(env), reading nothing when it doesn't exist."""
    config = load_config(None, {"XDG_CONFIG_HOME": str(tmp_path)}, tmp_path / "config.toml")
    assert config.coordinator == "127.0.0.1:20408"
    assert config.coordinator_source == "default"
