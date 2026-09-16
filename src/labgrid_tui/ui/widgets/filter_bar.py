"""Filter input and the predicate the device table applies."""

from textual.message import Message
from textual.widgets import Input

from labgrid_tui.coordinator.models import Place


def matches_filter(place: Place, caps: set[str], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    haystack = [place.name, place.comment, *caps]
    haystack += [f"{k}={v}" for k, v in place.tags.items()]
    return any(q in item.lower() for item in haystack)


class FilterBar(Input):
    class FilterChanged(Message):
        def __init__(self, query: str) -> None:
            super().__init__()
            self.query = query

    def __init__(self) -> None:
        super().__init__(
            id="filter-bar",
            placeholder="filter: name, comment, tag=value, resource",
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.FilterChanged(event.value))
