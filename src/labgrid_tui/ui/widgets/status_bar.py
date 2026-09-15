"""Status bar rendered from pluggable, priority-ordered segment providers.

Generic on purpose: a downstream shell appends its own providers (spec
3.2) without this package knowing what they show; those extension
segments get a fixed, early-drop priority since this package has no way
to judge their importance relative to its own.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from textual import events
from textual.widgets import Static

from labgrid_tui.ui.layout import middle_ellipsis

logger = logging.getLogger(__name__)

SegmentProvider = Callable[[], str | None]

# Priority tiers: lower drops (or degrades) first once the joined line no
# longer fits the bar's width. A segment's own `degraded` variant, if any,
# is tried before it is dropped outright.
PRIORITY_VERSION = 0
PRIORITY_EXTRA = 1
PRIORITY_SOURCE_SUFFIX = 2
PRIORITY_COPY_ONLY = 3
PRIORITY_COUNTS = 4
PRIORITY_CORE = 5


@dataclass(frozen=True)
class Segment:
    """One status-bar segment.

    ``degraded`` is tried, at this segment's own priority tier, before the
    segment is dropped outright. ``shrinkable`` marks a segment (at most
    one should be) as eligible for middle-ellipsis truncation as the final
    fallback once every priority tier has already been applied and the
    line still overflows.
    """

    provider: SegmentProvider
    priority: int = PRIORITY_CORE
    degraded: SegmentProvider | None = None
    shrinkable: bool = False


def _join(texts: Sequence[str | None]) -> str:
    return " | ".join(t for t in texts if t)


def _call_provider(provider: SegmentProvider) -> str | None:
    """Call a segment provider, treating any exception as "no segment".

    Providers include downstream extension hooks (``extra_status_segments``)
    this package does not control; one raising must not break the status
    bar for every other segment.
    """
    try:
        return provider()
    except Exception:
        logger.warning("status bar segment provider raised", exc_info=True)
        return None


class StatusBar(Static):
    def __init__(self, segments: Sequence[Segment]) -> None:
        super().__init__("", id="status-bar")
        self._segments = list(segments)
        # Tour pointer shown in front of the first segment (the coordinator).
        self.marker = ""

    def set_marker(self, marker: str) -> None:
        self.marker = marker
        self.refresh_status()

    def on_resize(self, _event: events.Resize) -> None:
        # The bar's own width just changed; a stale render from before the
        # resize would keep an already-dropped segment gone (or vice
        # versa) until the next unrelated refresh_status() call.
        self.refresh_status()

    def refresh_status(self) -> None:
        self.update(self._render_line())

    def _render_line(self) -> str:
        width = self.content_size.width
        texts: list[str | None] = [_call_provider(segment.provider) for segment in self._segments]
        if self.marker and texts and texts[0] is not None:
            texts[0] = f"{self.marker} {texts[0]}"
        if width <= 0:
            return _join(texts)

        order = sorted(range(len(self._segments)), key=lambda i: self._segments[i].priority)
        for index in order:
            if len(_join(texts)) <= width:
                break
            if texts[index] is None:
                continue
            segment = self._segments[index]
            texts[index] = (
                _call_provider(segment.degraded) if segment.degraded is not None else None
            )

        line = _join(texts)
        if len(line) > width:
            line = self._shrink_one(texts, width)
        if len(line) > width:
            # Absolute last resort (e.g. an unreasonably narrow bar):
            # hard-truncate the whole line rather than let a 1-row Static
            # wrap into, and clip, the row below it.
            line = middle_ellipsis(line, width)
        return line

    def _shrink_one(self, texts: list[str | None], width: int) -> str:
        shrink_index = next(
            (i for i, s in enumerate(self._segments) if s.shrinkable and texts[i]), None
        )
        if shrink_index is None:
            return _join(texts)
        overflow = len(_join(texts)) - width
        current = texts[shrink_index] or ""
        texts[shrink_index] = middle_ellipsis(current, max(0, len(current) - overflow))
        return _join(texts)
