"""Clipboard helpers for the TUI.

Two paths exist for getting text out of the TUI to the user's system
clipboard:

1. :func:`copy_via_osc52`: uses Textual's built-in OSC 52 escape
   sequence (``App.copy_to_clipboard``). Zero local dependency, works
   over SSH, but requires the terminal emulator to honour the escape.
   libVTE-based terminals on Debian (GNOME Terminal, xfce4-terminal,
   mate-terminal, tilix, lxterminal) silently drop OSC 52 until VTE 0.78
   (GNOME 46, late 2024), and even then default the toggle off. There is
   no protocol feedback: the TUI cannot tell whether the terminal
   accepted the sequence, so a "Copied" notification on its own is
   misleading on non-cooperating terminals.

2. :func:`copy_via_suspend`: releases the terminal back to its default
   mode via ``App.suspend()``, prints the text in plain text inside a
   clearly delimited block, and waits for Enter before resuming the TUI.
   The user copies with whatever mechanism their terminal already
   provides (mouse selection + Ctrl+Shift+C, right-click -> Copy,
   drag-select, etc.). Works on every terminal regardless of OSC 52
   policy because it does not rely on any escape sequence the terminal
   might choose to drop.

Screens that copy a command or device details bind ``Enter``/``y`` to
the OSC 52 path (fast, no flicker) and ``Shift+Enter`` to the suspend
fallback so users on Debian default terminals always have a working
option.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from textual.app import App


def copy_via_osc52(
    app: App[Any],
    text: str,
    *,
    label: str | None = None,
) -> None:
    """Copy *text* to the system clipboard via the OSC 52 escape.

    Args:
        app: The Textual application that owns the terminal.
        text: The plain-text payload to copy.
        label: Optional short description shown in the notification
            (e.g. ``"command"`` or ``"details for tb-1"``). When omitted
            the notification just says "Copied".

    Notes:
        Always emits a notification reminding the user about
        ``Shift+Enter`` as a fallback because OSC 52 silently fails on
        many Debian default terminals.
    """
    app.copy_to_clipboard(text)
    head = f"Copied: {label}" if label else "Copied"
    app.notify(
        f"{head}\nIf paste comes back empty, press Shift+Enter to copy via terminal selection.",
        timeout=5,
    )


def copy_via_suspend(
    app: App[Any],
    text: str,
    *,
    label: str = "text",
) -> None:
    """Suspend the TUI and print *text* for terminal-native copy.

    Releases the terminal back to its default cooked mode via
    ``App.suspend()`` so the terminal emulator's own selection and copy
    mechanisms work normally, prints *text* surrounded by a visible
    delimiter, and blocks on ``input()`` until the user presses Enter.

    Args:
        app: The Textual application to suspend.
        text: The plain-text payload to print.
        label: Short description used in the on-screen instruction (e.g.
            ``"command"``, ``"device details"``).

    Notes:
        Synchronous on purpose: the TUI is paused while the user copies,
        so blocking the event loop on ``input()`` is intentional.
        ``EOFError`` and ``KeyboardInterrupt`` during the prompt are
        swallowed so that piping input or pressing Ctrl+C does not crash
        the screen on resume.
    """
    bar = "=" * 64
    with app.suspend():
        # Stay close to the screen the user just left: print a clear
        # header, the payload, and a one-line prompt.
        print()
        print(bar)
        print(f"  Select the {label} below with your mouse, then copy")
        print("  it (Ctrl+Shift+C, right-click -> Copy, drag-select).")
        print(bar)
        print()
        print(text)
        print()
        with contextlib.suppress(EOFError, KeyboardInterrupt):
            input("Press Enter to return to the dashboard. ")
    app.notify(f"Returned from copy mode ({label})")
