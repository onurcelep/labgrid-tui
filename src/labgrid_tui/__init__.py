"""labgrid-tui: terminal dashboard for labgrid labs."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("labgrid-tui")
except PackageNotFoundError:  # running from a checkout that is not installed
    __version__ = "0.0.0"
