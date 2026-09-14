"""Entry-point plugin loading. The whole plugin surface: two dicts."""

import logging
from importlib.metadata import entry_points
from typing import NamedTuple

from labgrid_tui.model.commands import CommandTemplate

logger = logging.getLogger(__name__)

GROUP = "labgrid_tui.plugins"


class PluginData(NamedTuple):
    capabilities: dict[str, str]
    commands: dict[str, tuple[CommandTemplate, ...]]


def load_plugins() -> PluginData:
    capabilities: dict[str, str] = {}
    commands: dict[str, tuple[CommandTemplate, ...]] = {}
    for entry_point in entry_points(group=GROUP):
        try:
            module = entry_point.load()
            capabilities.update(getattr(module, "capabilities", {}))
            for cls_name, templates in getattr(module, "commands", {}).items():
                commands[cls_name] = commands.get(cls_name, ()) + tuple(templates)
        except Exception:
            logger.warning("plugin %s failed to load; skipping", entry_point.name, exc_info=True)
            continue
    return PluginData(capabilities, commands)
