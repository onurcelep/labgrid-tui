"""Single interleaved feed: coordinator events + command output.

Every activity line is a ``rich.text.Text`` built from a dim ``HH:MM:SS``
prefix, a per-kind icon, a bold subject, and a per-kind colored detail.
Raw command output (``log_output``) is written unformatted: it is
already-formatted external process output, not an activity message.
"""

from datetime import datetime

from rich.text import Text
from textual.widgets import RichLog

from labgrid_tui.model.events import (
    KIND_ACQUIRED,
    KIND_DELETED,
    KIND_ERROR,
    KIND_NEUTRAL,
    KIND_RELEASED,
    KIND_RESERVATION,
    KIND_RESOURCE_DELETED,
    KIND_RESOURCE_OFFLINE,
    KIND_RESOURCE_ONLINE,
    Kind,
)

_KIND_STYLES: dict[Kind, str] = {
    KIND_ACQUIRED: "yellow",
    KIND_RELEASED: "green",
    KIND_DELETED: "red",
    KIND_RESERVATION: "cyan",
    KIND_RESOURCE_ONLINE: "green",
    KIND_RESOURCE_OFFLINE: "red",
    KIND_RESOURCE_DELETED: "red dim",
    KIND_ERROR: "red",
    KIND_NEUTRAL: "",
}

_KIND_ICONS: dict[Kind, str] = {
    KIND_ACQUIRED: "▶",  # play / right triangle
    KIND_RELEASED: "■",  # stop / square
    KIND_DELETED: "✖",  # heavy X
    KIND_RESERVATION: "◆",  # diamond
    KIND_RESOURCE_ONLINE: "▲",  # up triangle
    KIND_RESOURCE_OFFLINE: "▼",  # down triangle
    KIND_RESOURCE_DELETED: "✖",
    KIND_ERROR: "✖",
    KIND_NEUTRAL: "",
}


class ActivityLog(RichLog):
    def __init__(self) -> None:
        # wrap=True: a long command line (log_output) must wrap rather than
        # clip at the log's right edge; RichLog re-wraps existing lines on
        # resize (see textual/widgets/_rich_log.py), so this keeps working
        # as the activity log's height/width changes across breakpoints.
        super().__init__(id="activity-log", max_lines=1000, markup=False, wrap=True)

    def log_line(self, line: str) -> None:
        """Plain message: neutral kind, no icon, no bold subject."""
        self.write(format_line(KIND_NEUTRAL, "", line))

    def log_event(self, kind: Kind, subject: str, detail: str) -> None:
        """Icon'd message: dim timestamp, kind icon, bold subject, colored detail."""
        self.write(format_line(kind, subject, detail))

    def log_output(self, line: str) -> None:
        """Raw external process output line: no timestamp, no icon."""
        self.write(Text(line))


def format_line(kind: Kind, subject: str, detail: str) -> Text:
    """Format one activity line as a Rich ``Text``.

    Never pass a user-controlled string through markup: this widget stays
    ``RichLog(markup=False)`` and writes ``Text`` objects exclusively.
    """
    ts = datetime.now().strftime("%H:%M:%S")
    # "•" is the fallback for a kind that isn't in _KIND_ICONS, so a
    # mismapped kind stays visible instead of vanishing.
    icon = _KIND_ICONS.get(kind, "•")
    style = _KIND_STYLES.get(kind, "")

    text = Text()
    text.append(f"{ts} ", style="dim")
    if icon:
        text.append(f"{icon} ", style=style)
    if subject:
        text.append(subject, style="bold")
        text.append(f" {detail}", style=style)
    else:
        text.append(detail, style=style)
    return text
