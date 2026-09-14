"""Attribute stream resources to places via labgrid's match-pattern semantics.

Mirrors upstream ResourceMatch.ismatch: fnmatchcase per component; the name
component only participates when both resource name and pattern name are
non-empty.
"""

from collections.abc import Iterable
from fnmatch import fnmatchcase

from labgrid_tui.coordinator.models import Place, Resource, ResourceMatchPattern


def pattern_matches(pattern: ResourceMatchPattern, resource: Resource) -> bool:
    if not fnmatchcase(resource.exporter, pattern.exporter):
        return False
    if not fnmatchcase(resource.group, pattern.group):
        return False
    if not fnmatchcase(resource.cls, pattern.cls):
        return False
    return not resource.name or not pattern.name or fnmatchcase(resource.name, pattern.name)


def resources_for_place(place: Place, resources: Iterable[Resource]) -> list[Resource]:
    return [r for r in resources if any(pattern_matches(m, r) for m in place.matches)]
