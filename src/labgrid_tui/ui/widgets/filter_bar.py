"""Filter input and the predicate the device table applies."""

from textual.message import Message
from textual.widgets import Input

from labgrid_tui.coordinator.models import Place


def matches_filter(place: Place, caps: set[str], query: str) -> bool:
    q = query.strip().lower()
    if not q:
        return True
    # Aliases render beside the name in the table, and labgrid-client
    # accepts one wherever it accepts a place name, so typing one here
    # has to find the place it belongs to.
    haystack = [place.name, *place.aliases, place.comment, *caps]
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
            placeholder="filter: name, alias, comment, tag=value, resource",
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.FilterChanged(event.value))
