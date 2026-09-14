from pathlib import Path

import pytest

from labgrid_tui.__main__ import main


def test_lg_proxy_refused(monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture[str],
                          tmp_path: Path) -> None:
    # Isolate from the developer's real config file: a stray config.toml
    # under their actual XDG_CONFIG_HOME must never influence this test.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("LG_PROXY", "hop.lab")
    assert main([]) == 1
    assert "LG_PROXY" in capsys.readouterr().err


def test_flag_parsing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    class FakeApp:
        def __init__(self, config: object) -> None:
            seen["coordinator"] = config.coordinator  # type: ignore[attr-defined]

        def run(self) -> None:
            pass

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("LG_PROXY", raising=False)
    monkeypatch.setattr("labgrid_tui.__main__.LabgridTuiApp", FakeApp)
    assert main(["-x", "coord.lab"]) == 0
    assert seen["coordinator"] == "coord.lab:20408"
