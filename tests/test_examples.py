"""Checks on examples/lab/, the bridge from the tour to a real coordinator.

The bench names and tag keys come from the tour's own fleet, so changing
the tour without updating the example files fails here instead of leaving
a user copying a config that no longer matches what they were shown.

PyYAML is not a dependency of this project, so exporter.yaml is checked as
text rather than parsed.
"""

import os
import stat
from pathlib import Path

from labgrid_tui.tour.fleet import EXPORTER, LAB_SCRIPT

LAB = Path(__file__).resolve().parent.parent / "examples" / "lab"
EXPORTER_YAML = LAB / "exporter.yaml"
PLACES_SH = LAB / "places.sh"

BENCHES = tuple(place.name for place, _resources in LAB_SCRIPT.initial)
TAG_KEYS = ("board", "env", "site")


def test_exporter_yaml_has_a_group_per_tour_bench() -> None:
    lines = EXPORTER_YAML.read_text().splitlines()
    groups = [line[:-1] for line in lines if line.endswith(":") and not line.startswith((" ", "#"))]
    assert groups == list(BENCHES)


def test_exporter_yaml_comments_survive_labgrid_jinja_preprocessing() -> None:
    # labgrid renders the exporter config through Jinja2 with "#" as the
    # line statement prefix, so a comment must start with "##" or the
    # exporter fails to start on an unknown tag.
    offenders = [
        line
        for line in EXPORTER_YAML.read_text().splitlines()
        if line.lstrip().startswith("#") and not line.lstrip().startswith("##")
    ]
    assert offenders == []


def test_places_script_is_an_executable_posix_shell_script() -> None:
    assert os.stat(PLACES_SH).st_mode & stat.S_IXUSR
    text = PLACES_SH.read_text()
    assert text.startswith("#!/bin/sh\n")
    assert "set -eu" in text


def test_places_script_covers_every_bench_and_tag_key() -> None:
    text = PLACES_SH.read_text()
    for bench in BENCHES:
        assert bench in text
    for key in TAG_KEYS:
        assert f"{key}=" in text
    assert EXPORTER in text
    for verb in ("create", "add-match", "set-tags", "delete"):
        assert verb in text


def test_places_script_declares_every_alias_the_tour_shows() -> None:
    text = PLACES_SH.read_text()
    assert "add-alias" in text
    for place, _resources in LAB_SCRIPT.initial:
        for alias in place.aliases:
            assert f"{place.name}:{alias}" in text
