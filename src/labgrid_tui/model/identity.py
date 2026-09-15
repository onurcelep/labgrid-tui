"""Who "me" is, resolved exactly the way labgrid-client resolves it."""

import enum
import getpass
import os
import socket

from labgrid_tui.coordinator.models import Place


def current_user() -> str:
    return os.environ.get("LG_USERNAME", getpass.getuser())


def current_host() -> str:
    return os.environ.get("LG_HOSTNAME", socket.gethostname())


def current_id() -> str:
    return f"{current_host()}/{current_user()}"


class Access(enum.Enum):
    USABLE = enum.auto()
    NOT_ACQUIRED = enum.auto()
    OTHER_USER = enum.auto()
    OTHER_HOST = enum.auto()


def place_access(place: Place, me: str) -> Access:
    if not place.acquired:
        return Access.NOT_ACQUIRED
    if place.acquired == me or me in place.allowed:
        return Access.USABLE
    host, _, user = place.acquired.partition("/")
    _, _, my_user = me.partition("/")
    if user == my_user:
        return Access.OTHER_HOST
    return Access.OTHER_USER


def access_reason(access: Access, place: Place, me: str) -> str | None:
    if access is Access.USABLE:
        return None
    if access is Access.NOT_ACQUIRED:
        return "hold the bench first"
    host, _, user = (place.acquired or "").partition("/")
    if access is Access.OTHER_USER:
        return f"held by {user}"
    return f"held by you on {host}; run allow {me} there"
