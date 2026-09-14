"""Built-in "robot" command pack shown during the tour.

Same shape as examples/robot.toml, but loaded from a string: the tour must
never read packs.toml or any file the pack registry would otherwise touch.
"""

from labgrid_tui.model.packs import Pack
from labgrid_tui.packs import parse_pack_bytes

ROBOT_PACK_TOML = """\
[pack]
name = "robot"
description = "Robot Framework recipes (tour example)"

[[commands]]
label = "Smoke tests"
command = "robot -v PLACE:{place} -v DUT_IP:{res.NetworkService.address} tests/smoke"
requires = ["res.NetworkService"]

[[commands]]
label = "Full suite (acquired only)"
command = "robot -v PLACE:{place} -v DUT_IP:{res.NetworkService.address} tests/"
requires = ["res.NetworkService", "held"]

[[commands]]
label = "Tag the run"
command = "robot -v PLACE:{place} -v BOARD:{tag.board} tests/smoke"
requires = ["tag.board"]
"""


def load_robot_pack() -> Pack:
    pack, errors = parse_pack_bytes(ROBOT_PACK_TOML.encode(), "tour:robot")
    if pack is None:
        # Unreachable in practice: ROBOT_PACK_TOML is fixed and covered by
        # tests/tour/test_fleet.py. Fails loudly rather than silently
        # showing an empty pack tab if it ever does drift.
        raise AssertionError(f"tour's built-in robot pack failed to parse: {errors}")
    return pack
