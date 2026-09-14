"""Unit tests for TourActionRunner: copy-only, and run() keeps the same
RUNNABLE guard CliActionRunner.run enforces (regression: run_entry, reachable
from the command palette, does not pre-filter by state the way
DashboardScreen._dispatch_verb does)."""

from labgrid_tui.model.commands import CommandEntry, CommandTemplate, EntryState
from labgrid_tui.tour.runner import TourActionRunner


def _runner() -> tuple[TourActionRunner, list[str], list[CommandEntry]]:
    copied: list[str] = []
    on_copy_calls: list[CommandEntry] = []

    runner = TourActionRunner(
        copy_to_clipboard=copied.append,
        spawn=lambda coro: coro.close(),
        activity=lambda kind, subject, detail: None,
        output=lambda line: None,
        notify=lambda message: None,
        on_copy=on_copy_calls.append,
    )
    return runner, copied, on_copy_calls


def _entry(state: EntryState, reason: str | None = None) -> CommandEntry:
    template = CommandTemplate("Manage", "Acquire", "acquire")
    return CommandEntry(template, "labgrid-client -p bench-01 acquire", state, reason)


def test_run_copies_a_runnable_entry() -> None:
    runner, copied, on_copy_calls = _runner()
    entry = _entry(EntryState.RUNNABLE)
    runner.run(entry)
    assert copied == [entry.command_line]
    assert on_copy_calls == [entry]


def test_run_does_nothing_for_an_unavailable_entry() -> None:
    runner, copied, on_copy_calls = _runner()
    entry = _entry(EntryState.UNAVAILABLE, "already acquired by someone")
    runner.run(entry)
    assert copied == []
    assert on_copy_calls == []


def test_copy_always_copies_regardless_of_state() -> None:
    """copy() (used directly by overlays' enter/y) is unconditional: only
    run()'s dispatch path is gated by state."""
    runner, copied, on_copy_calls = _runner()
    entry = _entry(EntryState.UNAVAILABLE, "needs res.NetworkService")
    runner.copy(entry)
    assert copied == [entry.command_line]
    assert on_copy_calls == [entry]
