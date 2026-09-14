"""Tests for the TUI clipboard helpers."""

from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from labgrid_tui.ui.clipboard import copy_via_osc52, copy_via_suspend

# ---------------------------------------------------------------------------
# copy_via_osc52
# ---------------------------------------------------------------------------


class TestCopyViaOsc52:
    """Behaviour of the OSC-52 copy path."""

    def test_calls_app_copy_to_clipboard(self) -> None:
        app = MagicMock()
        copy_via_osc52(app, "labgrid-client -p tb-1 acquire")
        app.copy_to_clipboard.assert_called_once_with("labgrid-client -p tb-1 acquire")

    def test_emits_notification_with_fallback_hint(self) -> None:
        """Users must be told about Shift+Enter when paste turns up empty."""
        app = MagicMock()
        copy_via_osc52(app, "echo hi", label="echo hi")
        app.notify.assert_called_once()
        message = app.notify.call_args[0][0]
        assert "Copied: echo hi" in message
        assert "Shift+Enter" in message

    def test_label_omitted_uses_generic_copied(self) -> None:
        app = MagicMock()
        copy_via_osc52(app, "secret-data")
        message = app.notify.call_args[0][0]
        assert message.startswith("Copied\n")
        # Payload should not leak into the notification when no label
        assert "secret-data" not in message


# ---------------------------------------------------------------------------
# copy_via_suspend
# ---------------------------------------------------------------------------


class _SuspendApp:
    """Minimal Textual ``App``-like object for suspend tests.

    We can't use a real ``App`` here because ``run_test()`` does not
    expose an event loop that pairs with a real terminal, so
    ``suspend()`` would raise. The helper uses the duck-typed methods we
    exercise: ``suspend()`` (context manager), ``notify(text)``.
    """

    def __init__(self) -> None:
        self.suspend_calls = 0
        self.notifications: list[str] = []

    @contextlib.contextmanager
    def suspend(self) -> Any:
        self.suspend_calls += 1
        yield

    def notify(
        self,
        message: str,
        *,
        timeout: float | None = None,
        severity: str = "information",
    ) -> None:
        self.notifications.append(message)


class TestCopyViaSuspend:
    """Behaviour of the suspend-and-print fallback."""

    def test_uses_app_suspend(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The TUI must release the terminal before printing."""
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        app = _SuspendApp()
        copy_via_suspend(app, "payload", label="command")  # type: ignore[arg-type]
        assert app.suspend_calls == 1

    def test_prints_payload_inside_delimiters(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The block must visibly delimit the copy region."""
        monkeypatch.setattr("builtins.input", lambda _prompt="": "")
        app = _SuspendApp()
        copy_via_suspend(
            app,  # type: ignore[arg-type]
            "labgrid-client -p tb-1 acquire",
            label="command",
        )
        out = capsys.readouterr().out
        assert "labgrid-client -p tb-1 acquire" in out
        # Visible bar surrounds the instructions
        assert "=" * 16 in out
        assert "Select the command below" in out

    def test_eof_during_prompt_is_swallowed(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Piped stdin or an unexpected EOF must not crash the screen."""

        def _raise(_prompt: str = "") -> str:
            raise EOFError

        monkeypatch.setattr("builtins.input", _raise)
        app = _SuspendApp()
        # Must not raise
        copy_via_suspend(app, "payload", label="command")  # type: ignore[arg-type]
        # Notification still emitted on resume so the user sees feedback
        assert any("Returned from copy mode" in n for n in app.notifications)

    def test_keyboard_interrupt_during_prompt_is_swallowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ctrl+C at the prompt must return cleanly to the TUI."""

        def _raise(_prompt: str = "") -> str:
            raise KeyboardInterrupt

        monkeypatch.setattr("builtins.input", _raise)
        app = _SuspendApp()
        copy_via_suspend(app, "payload", label="command")  # type: ignore[arg-type]
        assert app.suspend_calls == 1
