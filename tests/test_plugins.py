import types
from typing import Any

import pytest

import labgrid_tui.plugins as plugins_mod
from labgrid_tui.model.commands import CommandTemplate
from labgrid_tui.plugins import load_plugins


class _FakeEntryPoint:
    def __init__(self, module: Any, name: str = "fake") -> None:
        self._module = module
        self.name = name

    def load(self) -> Any:
        if isinstance(self._module, Exception):
            raise self._module
        return self._module


def _patch(monkeypatch: pytest.MonkeyPatch, *eps: _FakeEntryPoint) -> None:
    monkeypatch.setattr(plugins_mod, "entry_points", lambda group: list(eps))


def test_no_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    data = load_plugins()
    assert data.capabilities == {}
    assert data.commands == {}


def test_plugin_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.SimpleNamespace(
        capabilities={"MyResource": "magic"},
        commands={"MyResource": [CommandTemplate("Magic", "Zap", "zap")]},
    )
    _patch(monkeypatch, _FakeEntryPoint(mod))
    data = load_plugins()
    assert data.capabilities == {"MyResource": "magic"}
    assert data.commands["MyResource"][0].label == "Zap"


def test_broken_plugin_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    good = types.SimpleNamespace(capabilities={"A": "a"})
    _patch(monkeypatch, _FakeEntryPoint(RuntimeError("boom"), name="bad"),
           _FakeEntryPoint(good, name="good"))
    data = load_plugins()
    assert data.capabilities == {"A": "a"}


def test_plugin_missing_attrs_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeEntryPoint(types.SimpleNamespace()))
    data = load_plugins()
    assert data.capabilities == {}


def test_plugin_commands_concat(monkeypatch: pytest.MonkeyPatch) -> None:
    plugin1 = types.SimpleNamespace(
        commands={"MyResource": [CommandTemplate("Magic", "Zap", "zap")]}
    )
    plugin2 = types.SimpleNamespace(
        commands={"MyResource": [CommandTemplate("Power", "Boom", "boom")]}
    )
    _patch(monkeypatch, _FakeEntryPoint(plugin1, name="p1"),
           _FakeEntryPoint(plugin2, name="p2"))
    data = load_plugins()
    assert len(data.commands["MyResource"]) == 2
    assert data.commands["MyResource"][0].label == "Zap"
    assert data.commands["MyResource"][1].label == "Boom"


def test_broken_plugin_malformed_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    broken = types.SimpleNamespace(commands={"X": 42})
    good = types.SimpleNamespace(capabilities={"B": "b"})
    _patch(monkeypatch, _FakeEntryPoint(broken, name="broken"),
           _FakeEntryPoint(good, name="good"))
    data = load_plugins()
    assert data.capabilities == {"B": "b"}
    assert data.commands == {}
