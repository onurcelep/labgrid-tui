"""Activity-log event kind vocabulary.

Lives outside ui/widgets so both the presentation layer (ActivityLog) and
the execution layer (CliActionRunner in ui/actions.py) can import it
without either depending on the other.
"""

from typing import Final, Literal

Kind = Literal[
    "place_acquired",
    "place_released",
    "place_deleted",
    "resource_online",
    "resource_offline",
    "resource_deleted",
    "reservation_changed",
    "error",
    "neutral",
]

KIND_ACQUIRED: Final[Kind] = "place_acquired"
KIND_RELEASED: Final[Kind] = "place_released"
KIND_DELETED: Final[Kind] = "place_deleted"
KIND_RESOURCE_ONLINE: Final[Kind] = "resource_online"
KIND_RESOURCE_OFFLINE: Final[Kind] = "resource_offline"
KIND_RESOURCE_DELETED: Final[Kind] = "resource_deleted"
KIND_RESERVATION: Final[Kind] = "reservation_changed"
# Unlike the other kinds, error/neutral don't come from coordinator
# events: they cover local command output (a run's exit status, plain
# log lines) interleaved into the same feed.
KIND_ERROR: Final[Kind] = "error"
KIND_NEUTRAL: Final[Kind] = "neutral"
