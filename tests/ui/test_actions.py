"""CliActionRunner dispatch matrix, tested without a running App."""

import asyncio

import pytest

from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState
from labgrid_tui.model.events import KIND_ERROR, KIND_NEUTRAL
from labgrid_tui.ui.actions import CliActionRunner


def _entry(
    *, needs_args: bool = False, interactive: bool = False, state: EntryState = EntryState.RUNNABLE
) -> CommandEntry:
    template = CommandTemplate("T", "Test", "test", interactive=interactive, needs_args=needs_args)
    return CommandEntry(template, "labgrid-client -p tb-1 test", state, None)


class Recorder:
    def __init__(self) -> None:
        self.clipboard: list[str] = []
        self.activity: list[tuple[str, str, str]] = []
        self.output: list[str] = []
        self.spawned: list[object] = []
        self.notified: list[str] = []

    def spawn(self, coro: object) -> None:
        self.spawned.append(coro)

    def _activity(self, kind: str, subject: str, detail: str) -> None:
        self.activity.append((kind, subject, detail))

    def runner(self) -> CliActionRunner:
        return CliActionRunner(
            copy_to_clipboard=self.clipboard.append,
            spawn=self.spawn,
            activity=self._activity,
            output=self.output.append,
            notify=self.notified.append,
        )


def test_non_runnable_is_ignored() -> None:
    rec = Recorder()
    rec.runner().run(_entry(state=EntryState.UNAVAILABLE))
    assert rec.clipboard == [] and rec.activity == [] and rec.spawned == []


def test_needs_args_routes_to_copy() -> None:
    rec = Recorder()
    rec.runner().run(_entry(needs_args=True))
    assert rec.clipboard == ["labgrid-client -p tb-1 test"]
    assert any("placeholders" in n for n in rec.notified)
    assert any("copied" in detail for _, _, detail in rec.activity)
    assert all(kind == KIND_NEUTRAL for kind, _, _ in rec.activity)
    assert rec.spawned == []


def test_missing_client_routes_to_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: False)
    rec = Recorder()
    rec.runner().run(_entry())
    assert rec.clipboard == ["labgrid-client -p tb-1 test"]


def test_interactive_copies_with_notice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)
    rec = Recorder()
    rec.runner().run(_entry(interactive=True))
    assert rec.clipboard == ["labgrid-client -p tb-1 test"]
    assert any("another terminal" in n for n in rec.notified)
    assert rec.spawned == []


def test_capture_spawns_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)

    async def fake_capture(line: str, on_line: object) -> int:
        return 0

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", fake_capture)
    rec = Recorder()
    rec.runner().run(_entry())
    assert any(detail.startswith("$ ") for _, _, detail in rec.activity)
    assert len(rec.spawned) == 1
    coro = rec.spawned[0]
    asyncio.run(coro)  # type: ignore[arg-type]
    assert any("[exit 0]" in detail for _, _, detail in rec.activity)
    assert all(kind == KIND_NEUTRAL for kind, _, _ in rec.activity)
    assert len(rec.notified) == 1
    assert "succeeded" in rec.notified[0]
    assert "labgrid-client -p tb-1 test" in rec.notified[0]


def test_capture_failure_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)

    async def fake_capture(line: str, on_line: object) -> int:
        return 2

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", fake_capture)
    rec = Recorder()
    rec.runner().run(_entry())
    coro = rec.spawned[0]
    asyncio.run(coro)  # type: ignore[arg-type]
    assert any("[exit 2]" in detail for _, _, detail in rec.activity)
    assert any(kind == KIND_ERROR for kind, _, _ in rec.activity)
    assert len(rec.notified) == 1
    assert "exit 2" in rec.notified[0]
    assert "labgrid-client -p tb-1 test" in rec.notified[0]


def test_notify_success_false_suppresses_success_toast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)

    async def fake_capture(line: str, on_line: object) -> int:
        return 0

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", fake_capture)
    rec = Recorder()
    rec.runner().run(_entry(), notify_success=False)
    asyncio.run(rec.spawned[0])  # type: ignore[arg-type]
    assert any("[exit 0]" in detail for _, _, detail in rec.activity)
    assert rec.notified == []  # bulk dispatch: silent per item on success


def test_notify_success_false_still_notifies_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)

    async def fake_capture(line: str, on_line: object) -> int:
        return 2

    monkeypatch.setattr("labgrid_tui.ui.actions.run_capture", fake_capture)
    rec = Recorder()
    rec.runner().run(_entry(), notify_success=False)
    asyncio.run(rec.spawned[0])  # type: ignore[arg-type]
    assert len(rec.notified) == 1
    assert "exit 2" in rec.notified[0]


def test_copy_only_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pack entries (CommandTemplate.copy_only) must never reach
    run_capture, regardless of client availability: this is the one
    choke point every `.run()` call (verb keys, bulk dispatch) funnels
    through, so it is what actually keeps a pack entry from executing."""
    monkeypatch.setattr("labgrid_tui.ui.actions.client_available", lambda: True)
    rec = Recorder()
    template = CommandTemplate("robot", "Smoke tests", "robot tests/smoke", copy_only=True)
    entry = CommandEntry(template, "robot tests/smoke", EntryState.RUNNABLE, None)
    rec.runner().run(entry)
    assert rec.clipboard == ["robot tests/smoke"]
    assert rec.spawned == []
    assert rec.notified == ["Copied: robot tests/smoke"]


def test_copy() -> None:
    rec = Recorder()
    rec.runner().copy(_entry())
    assert rec.clipboard == ["labgrid-client -p tb-1 test"]
    assert rec.notified == ["Copied: test"]  # every copy toasts
