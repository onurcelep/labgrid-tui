"""Modal shown when the coordinator stays unreachable past a grace period."""

import contextlib

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Static

from labgrid_tui.coordinator.stream import ConnectionChanged, ConnState, Event, RetryScheduled


class ConnectionOverlay(ModalScreen[None]):
    DEFAULT_CSS = """
    ConnectionOverlay { align: center middle; }
    /* A definite width is required: with width auto the Static children
       (default width 1fr) resolve to zero columns and the box collapses
       to its border and padding. */
    #conn-body {
        width: 72;
        max-width: 95%;
        height: auto;
        border: thick $warning;
        background: $surface;
        padding: 1 3;
    }
    """

    # ModalScreen key handling stops the app-level BINDINGS chain at this
    # screen (Screen._modal_binding_chain), so LabgridTuiApp's own "q"
    # binding never reaches here; quit must be bound locally too.
    BINDINGS = [
        Binding("escape", "quit_app", "Quit", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("r", "retry_now", "Retry now", show=False),
    ]

    def __init__(self, coordinator: str, source: str, initial_event: Event | None = None) -> None:
        super().__init__()
        self._coordinator = coordinator
        self._source = source
        self._remaining = 0
        self._countdown_timer: Timer | None = None
        # Applied in on_mount rather than here: apply_stream_event queries
        # #conn-status, which does not exist until compose has run.
        self._initial_event = initial_event

    def compose(self) -> ComposeResult:
        text = (
            f"coordinator unreachable\n\n"
            f"address: {self._coordinator} (from {self._source})\n"
            f"check the address, your network, or LG_COORDINATOR"
        )
        with Vertical(id="conn-body"):
            yield Static(text, id="conn-text")
            yield Static("retrying with backoff...", id="conn-status")
            yield Static("esc/q to quit - r to retry now", id="conn-hint")

    def on_mount(self) -> None:
        if self._initial_event is not None:
            self.apply_stream_event(self._initial_event)

    def apply_stream_event(self, event: Event) -> None:
        """Update the countdown from a stream event forwarded by the app."""
        if isinstance(event, RetryScheduled):
            self._start_countdown(event.delay, event.attempt)
        elif isinstance(event, ConnectionChanged) and event.state is ConnState.CONNECTING:
            self._show_connecting()

    def _start_countdown(self, delay: float, attempt: int) -> None:
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
        self._remaining = max(1, round(delay))
        self._set_status(f"Retrying in {self._remaining}s ... (attempt {attempt})")
        self._countdown_timer = self.set_interval(1, self._tick)

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            if self._countdown_timer is not None:
                self._countdown_timer.stop()
                self._countdown_timer = None
            return
        self._set_status(f"Retrying in {self._remaining}s ...")

    def _show_connecting(self) -> None:
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None
        self._set_status("Connecting ...")

    def _set_status(self, text: str) -> None:
        with contextlib.suppress(NoMatches):
            self.query_one("#conn-status", Static).update(text)

    def action_quit_app(self) -> None:
        self.app.exit()

    def action_retry_now(self) -> None:
        retry = getattr(self.app, "retry_connection", None)
        if retry is not None:
            retry()

    def on_unmount(self) -> None:
        if self._countdown_timer is not None:
            self._countdown_timer.stop()
            self._countdown_timer = None
