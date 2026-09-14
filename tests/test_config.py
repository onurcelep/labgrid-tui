from pathlib import Path

import pytest

from labgrid_tui.config import default_config_path, load_config, resolve_coordinator
from labgrid_tui.coordinators import CoordinatorError


def test_flag_wins() -> None:
    assert resolve_coordinator("coord.lab:1234", {"LG_COORDINATOR": "env.lab"}) == (
        "coord.lab:1234",
        "flag",
    )


def test_env_second() -> None:
    assert resolve_coordinator(None, {"LG_COORDINATOR": "env.lab:999"}) == (
        "env.lab:999",
        "env",
    )


def test_default_last() -> None:
    assert resolve_coordinator(None, {}) == ("127.0.0.1:20408", "default")


def test_default_port_appended() -> None:
    assert resolve_coordinator("coord.lab", {}) == ("coord.lab:20408", "flag")
    assert resolve_coordinator(None, {"LG_COORDINATOR": "env.lab"}) == (
        "env.lab:20408",
        "env",
    )


def test_missing_config_file_is_fine(tmp_path: Path) -> None:
    config = load_config(None, {}, tmp_path / "nope.toml")
    assert config.coordinator == "127.0.0.1:20408"
    assert config.prefix is None
    assert config.capability_overrides == {}
    assert config.proxy_set is False


def test_config_file_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('prefix = "mycli"\n[capabilities]\nMyResource = "power"\n')
    config = load_config(None, {}, path)
    assert config.prefix == "mycli"
    assert config.capability_overrides == {"MyResource": "power"}


def test_malformed_config_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("prefix = [unterminated\n")
    config = load_config(None, {}, path)
    assert config.prefix is None
    assert config.capability_overrides == {}
    assert config.config_error is not None
    assert str(path) in config.config_error


def test_proxy_detected() -> None:
    config = load_config(None, {"LG_PROXY": "hop.lab"}, Path("/nonexistent"))
    assert config.proxy_set is True


def test_default_config_path_xdg() -> None:
    path = default_config_path({"XDG_CONFIG_HOME": "/tmp/xdg"})
    assert path == Path("/tmp/xdg/labgrid-tui/config.toml")


def test_command_templates_parsed(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[[commands]]\n"
        'category = "Custom"\n'
        'label = "Ping DUT"\n'
        'suffix = "ssh -- ping -c1 10.0.0.1"\n'
        'class = "NetworkService"\n'
        "\n"
        "[[commands]]\n"
        'category = "Custom"\n'
        'label = "Monitor"\n'
        'suffix = "monitor"\n'
    )
    config = load_config(None, {}, path)
    assert config.config_error is None
    assert config.command_templates["NetworkService"][0].label == "Ping DUT"
    assert config.place_templates[0].label == "Monitor"


def test_coordinator_from_config_used(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coordinator = "coord.lab:1234"\n')
    config = load_config(None, {}, path)
    assert config.coordinator == "coord.lab:1234"
    assert config.coordinator_source == "config"
    assert config.config_error is None


def test_coordinator_from_config_gets_default_port(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coordinator = "coord.lab"\n')
    config = load_config(None, {}, path)
    assert config.coordinator == "coord.lab:20408"
    assert config.coordinator_source == "config"


def test_flag_overrides_config_coordinator(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coordinator = "coord.lab:1234"\n')
    config = load_config("flag.lab:5", {}, path)
    assert config.coordinator == "flag.lab:5"
    assert config.coordinator_source == "flag"


def test_env_overrides_config_coordinator(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coordinator = "coord.lab:1234"\n')
    config = load_config(None, {"LG_COORDINATOR": "env.lab:9"}, path)
    assert config.coordinator == "env.lab:9"
    assert config.coordinator_source == "env"


def test_flag_bare_host_gets_default_port() -> None:
    config = load_config("coord.lab", {}, Path("/nonexistent"))
    assert config.coordinator == "coord.lab:20408"
    assert config.coordinator_source == "flag"


def test_flag_invalid_port_is_a_usage_error() -> None:
    """Unlike LG_COORDINATOR/config.toml, an invalid -x is not a
    fall-through condition: the caller explicitly asked for this address."""
    with pytest.raises(CoordinatorError):
        load_config("host:notaport", {}, Path("/nonexistent"))


def test_flag_malformed_address_is_a_usage_error() -> None:
    with pytest.raises(CoordinatorError):
        load_config("too:many:colons:here", {}, Path("/nonexistent"))


def test_env_bare_host_gets_default_port() -> None:
    config = load_config(None, {"LG_COORDINATOR": "env.lab"}, Path("/nonexistent"))
    assert config.coordinator == "env.lab:20408"
    assert config.coordinator_source == "env"


def test_env_invalid_port_falls_back_to_default(tmp_path: Path) -> None:
    config = load_config(None, {"LG_COORDINATOR": "host:notaport"}, tmp_path / "nope.toml")
    assert config.coordinator == "127.0.0.1:20408"
    assert config.coordinator_source == "default"
    assert config.config_error is not None
    assert "LG_COORDINATOR" in config.config_error


def test_env_invalid_port_falls_through_to_config_coordinator(tmp_path: Path) -> None:
    """An invalid LG_COORDINATOR is reported like a bad config.toml value
    and resolution keeps going, unlike an invalid -x."""
    path = tmp_path / "config.toml"
    path.write_text('coordinator = "coord.lab:1234"\n')
    config = load_config(None, {"LG_COORDINATOR": "host:notaport"}, path)
    assert config.coordinator == "coord.lab:1234"
    assert config.coordinator_source == "config"
    assert config.config_error is not None
    assert "LG_COORDINATOR" in config.config_error


def test_invalid_config_coordinator_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('coordinator = "not a valid host:with:extra:colons"\n')
    config = load_config(None, {}, path)
    assert config.coordinator == "127.0.0.1:20408"
    assert config.coordinator_source == "default"
    assert config.config_error is not None
    assert "coordinator" in config.config_error


def test_invalid_config_coordinator_wrong_type(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("coordinator = 1234\n")
    config = load_config(None, {}, path)
    assert config.coordinator == "127.0.0.1:20408"
    assert config.coordinator_source == "default"
    assert config.config_error is not None


def test_malformed_command_entries_counted_not_crashed(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[[commands]]\n"
        'label = "no category or suffix"\n'
        "\n"
        "[[commands]]\n"
        'category = "Ok"\n'
        'label = "Fine"\n'
        'suffix = "show"\n'
    )
    config = load_config(None, {}, path)
    assert config.place_templates[0].label == "Fine"
    assert config.config_error is not None and "invalid" in config.config_error


def test_flag_accepts_bracketed_ipv6(tmp_path: Path) -> None:
    config = load_config("[::1]:20408", {}, tmp_path / "config.toml")
    assert config.coordinator == "[::1]:20408"
    config = load_config("[::1]", {}, tmp_path / "config.toml")
    assert config.coordinator == "[::1]:20408"


def test_env_accepts_bracketed_ipv6(tmp_path: Path) -> None:
    config = load_config(None, {"LG_COORDINATOR": "[fe80::1]:20409"}, tmp_path / "config.toml")
    assert config.coordinator == "[fe80::1]:20409"
    assert config.coordinator_source == "env"
