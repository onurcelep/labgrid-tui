# labgrid-tui

A terminal dashboard for [labgrid](https://github.com/labgrid-project/labgrid)
labs. It connects to a labgrid coordinator over gRPC, shows every place and
resource live as it changes, and turns each one into the exact
`labgrid-client` command line to copy or edit. It has no state or protocol
of its own: a discoverability lens over what the coordinator already
exposes and what `labgrid-client` already does.

labgrid-tui is an independent, third-party project. It is not affiliated
with or endorsed by the labgrid project or its maintainers.

## At a glance

- **Live fleet view**: places, resources, holders and reservations from
  the coordinator's own event stream; no polling.
- **Every action is a real command line**: console, power, SSH, file
  transfer, SD-mux, video, acquire, release, shown as the exact
  `labgrid-client` invocation for that bench and gated by what the bench
  has and whether you hold it.
- **Several coordinators**: named entries, switched at runtime
  (`shift+p`) or from the CLI.
- **Shareable command packs**: a team's recipes in a small TOML file,
  placeholders filled in per bench, copy-only by design.
- **Reservations and an activity log**: queue for a busy bench, see the
  allocation happen, cancel with the token filled in.
- **Keyboard-first, read-only**: Vim keys, works in a narrow tmux pane,
  never mutates anything over gRPC, needs no credentials, manages no
  places.

## Install

```
uv tool install labgrid-tui
# or: uv tool install git+https://github.com/onurcelep/labgrid-tui
```

Python 3.11+. `labgrid-client` on `PATH` is optional: commands are copied
either way and run in place only when it is present.

## Usage

```
labgrid-tui config init     # optional: persist a coordinator address
labgrid-tui                 # or: labgrid-tui -x coordinator.example.org:20408
```

Press `?` in the dashboard for every key, the table legend and the
operations reference; the in-app help is the only copy of that material.

<details>
<summary><b>Configuration</b></summary>

Coordinator address, in order: `-x`, then `LG_COORDINATOR`, then the
active named coordinator, then `coordinator` in `config.toml`, then
`127.0.0.1:20408`. A bare host gets the default port. All files live in
`~/.config/labgrid-tui/` (`$XDG_CONFIG_HOME` honoured); none needs to
exist.

`config.toml` (hand-edited; `labgrid-tui config init` writes a commented
template, `config show` prints every resolved value and its source):

```toml
coordinator = "coordinator.example.org:20408"   # or a named coordinator
prefix = "labgrid-client -x coordinator.example.org:20408"   # optional

[capabilities]
MyCustomResource = "power"        # resource class -> capability chip

[[commands]]                      # extra labgrid-client entries
category = "Custom"
label = "Ping DUT"
suffix = "ssh -- ping -c1 10.0.0.1"
class = "NetworkService"          # omit for a place-level entry
```

Named coordinators live in `coordinators.toml`, written by the tool:
`shift+p` in the TUI (`enter` switch, `n` new, `e` edit, `x` delete) or
`labgrid-tui coordinator list|add|use|remove|show|edit`. `-x` and
`coordinator` accept a name from it as well as an address.

`LG_PROXY` is not supported: if it is set, labgrid-tui refuses to start.
UI state (panels, theme) persists to `~/.local/state/labgrid-tui/ui.toml`.

</details>

<details>
<summary><b>Command packs</b></summary>

A pack is a TOML file of copy-only recipes. Register it explicitly; nothing
is discovered on its own:

```
labgrid-tui pack add PATH|URL     # also: list, show, update, remove
```

A path is re-read on every start; an HTTPS URL is cached and refreshed by
`pack update`. Each pack is its own tab in the command overlay (`c`) and
its own palette group; `enter` copies, the TUI never executes a pack entry.

```toml
[pack]
name = "robot"

[[commands]]
label = "Smoke tests on this bench"
command = "robot -v PLACE:{place} -v DUT_IP:{res.NetworkService.address} tests/smoke"
requires = ["res.NetworkService"]
```

Placeholders: `{place}`, `{coordinator}`, `{prefix}`, `{token}` (your
reservation for this place), `{tag.KEY}`, `{res.CLASS.PARAM}` (first
matched resource of that class; `PARAM` may be `name`). `requires` items:
`res.CLASS`, `tag.KEY`, `held`. An unmet requirement or an unresolvable
placeholder greys the entry with the reason instead of failing. A full
example ships in [examples/robot.toml](examples/robot.toml).

</details>

<details>
<summary><b>Extending</b></summary>

Plugins are Python modules registered under the `labgrid_tui.plugins`
entry-point group that may define `capabilities: dict[str, str]`
(resource class to capability) and `commands: dict[str, tuple[CommandTemplate, ...]]`
(resource class to extra entries). A plugin that raises while loading is
skipped with a warning.

Building a larger shell on top (authentication, another execution
backend, extra status): every widget and screen takes its data through
constructor arguments, `LabgridTuiApp` is subclassable, and every action
is dispatched through one two-method protocol:

```python
class ActionRunner(Protocol):
    def run(self, entry: CommandEntry) -> None: ...
    def copy(self, entry: CommandEntry) -> None: ...
```

Hand the widgets your own runner and the validity engine (which commands
are offered, and why not) stays shared. Extra status-bar segments are
`Callable[[], str | None]` providers on `extra_status_segments`.
Unknown keys in `coordinators.toml` entries survive edits, so a shell can
store its own per-coordinator data there. This surface may change before
1.0.

</details>

<details>
<summary><b>Development</b></summary>

See [CONTRIBUTING.md](CONTRIBUTING.md) for the gate to run before opening a PR.

```
uv sync
uv run pre-commit install      # ruff check and format on every commit
uv run ruff check src tests
uv run mypy src
uv run pytest                  # unit and UI tests against a fake coordinator
```

- `tests/model` and `tests/coordinator`: pure logic and the gRPC layer.
- `tests/ui`: widgets and screens, driven through Textual's pilot.
- `tests/integration`: a real coordinator, excluded by default. Start one
  with `docker run -d -p 20408:20408 labgrid/coordinator` and run
  `uv run pytest -m integration` (`LABGRID_TUI_TEST_COORDINATOR` points
  elsewhere).
- `scripts/generate-stubs.sh` regenerates the gRPC stubs from the vendored
  proto.

Release: bump `version` in `pyproject.toml`, tag `vX.Y.Z`, push the tag.
The publish workflow uploads to PyPI through trusted publishing.

</details>

## License

Apache-2.0. Copyright 2026 Onur Celep. The package metadata declares
`Apache-2.0 AND LGPL-2.1-or-later` because of the vendored labgrid file below.

labgrid-tui does not include or import the labgrid Python package. It
contains an unmodified copy of labgrid's coordinator protocol definition
(`labgrid-coordinator.proto`, LGPL-2.1-or-later, licence text in
[LICENSE.labgrid](LICENSE.labgrid)) and gRPC stubs generated from it; it
talks to a labgrid coordinator over the network and runs `labgrid-client`
as a separate process.
