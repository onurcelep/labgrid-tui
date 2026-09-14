import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from labgrid_tui.ui.uistate import UiState, save_state, state_path
from tests.fake_coordinator import FakeCoordinator, start_fake


@pytest.fixture(autouse=True)
def _isolated_xdg_state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # LabgridTuiApp reads ui.toml from XDG_STATE_HOME on every construction;
    # a developer's real state file must never leak into, or be overwritten
    # by, a test run.
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))


@pytest.fixture(autouse=True)
def _seed_onboarded_ui_state(
    _isolated_xdg_state_home: None, request: pytest.FixtureRequest
) -> None:
    # LabgridTuiApp.on_mount arms one-shot 0.5s/4.0s onboarding toast timers
    # whenever ui.toml reports onboarded=False (the default for a fresh
    # XDG_STATE_HOME). Those timers can fire mid-test and either crash a
    # notify stub that doesn't expect them or pollute an exact-toast-list
    # assertion, so every test starts from an already-onboarded state file
    # unless it opts out with @pytest.mark.first_run.
    if request.node.get_closest_marker("first_run") is not None:
        return
    save_state(state_path(os.environ), UiState(onboarded=True))


@pytest.fixture(autouse=True)
def _isolated_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # LabgridTuiApp and load_config() both read config.toml and
    # coordinators.toml from XDG_CONFIG_HOME on every construction; a
    # developer's real files must never leak into, or be overwritten by, a
    # test run. Each autouse fixture gets its own tmp_path, so this is a
    # sibling directory of the state-home one above, not the same path.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))


@pytest.fixture(autouse=True)
def _no_real_labgrid_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # Verb dispatch shells out to labgrid-client. A developer machine usually
    # has it on PATH, so an unstubbed test would spawn real processes that can
    # reach a real coordinator; always dispatch against a fake unless a test
    # installs its own.
    async def _fake_capture(line: str, on_line: Callable[[str], None]) -> int:
        return 0

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", _fake_capture)
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)


@pytest.fixture
async def fake_coordinator() -> AsyncIterator[tuple[FakeCoordinator, str]]:
    servicer = FakeCoordinator()
    server, address = await start_fake(servicer)
    yield servicer, address
    await server.stop(grace=None)
