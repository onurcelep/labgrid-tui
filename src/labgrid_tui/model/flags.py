"""Curated per-subcommand option hints for edit-before-run.

Verified against the pinned labgrid-client argparse (see the vendored
proto's UPSTREAM rev). Keyed by the first token of a template's
cli_suffix; only options confirmed in that source belong here.
"""

COMMAND_FLAGS: dict[str, str] = {
    "console": "-l/--loop, -o/--listenonly, --logfile FILE",
    "power": "-t/--delay SECONDS (between off and on during cycle)",
    "reserve": "--wait, --prio N, --shell",
    "wait": "TOKEN",
    "video": "-q QUALITY ('list' shows options), -c CONTROLS (v4l, k=v pairs)",
    "ssh": "trailing arguments are passed to the ssh command",
    "scp": "prefix a path with : for the remote side",
    "rsync": "extra arguments are passed to rsync",
    "sshfs": "PATH MOUNTPOINT",
    "forward": "-L LOCAL:REMOTE, -R REMOTE:LOCAL",
    "io": "actions: high | low | get",
    "sd-mux": "modes: dut | host | off | client | get",
    "usb-mux": "links: off | dut-device | host-dut | host-device | host-dut+host-device",
    "dfu": "actions: download ALTSETTING FILE | detach ALTSETTING | list",
    "fastboot": "trailing arguments are passed to fastboot",
}


def flags_hint(cli_suffix: str) -> str | None:
    """The extra options the underlying subcommand accepts, if curated."""
    head = cli_suffix.split(maxsplit=1)[0] if cli_suffix else ""
    return COMMAND_FLAGS.get(head)
