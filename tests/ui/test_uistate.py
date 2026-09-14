from pathlib import Path

from labgrid_tui.ui.uistate import UiState, load_state, save_state, state_path


def test_state_path_xdg() -> None:
    assert state_path({"XDG_STATE_HOME": "/tmp/xs"}) == Path(
        "/tmp/xs/labgrid-tui/ui.toml"
    )


def test_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "ui.toml"
    save_state(path, UiState(theme="nord", show_activity=True, onboarded=True))
    state = load_state(path)
    assert state == UiState(theme="nord", show_activity=True, onboarded=True)


def test_missing_and_malformed_return_defaults(tmp_path: Path) -> None:
    assert load_state(tmp_path / "nope.toml") == UiState()
    bad = tmp_path / "bad.toml"
    bad.write_text("theme = [unclosed")
    assert load_state(bad) == UiState()


def test_bool_fields_type_gated(tmp_path: Path) -> None:
    bad = tmp_path / "bad_bool.toml"
    bad.write_text('show_activity = "false"\nonboarded = 1')
    assert load_state(bad) == UiState(show_activity=False, onboarded=False)


def test_save_failure_is_silent(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    save_state(blocker / "ui.toml", UiState())  # parent is a file: OSError path
