"""Help content: keybindings and labgrid-concepts primer, split by tab.

Each constant backs one ``TabPane`` in ``HelpOverlay``. Markdown tables and
paragraphs are used throughout instead of fenced code blocks: Textual's
``Markdown`` wraps table cells and paragraph text to the pane width, but
never wraps a fenced block (it scrolls horizontally instead), which is
exactly what clipped this content at narrow terminal widths.

``TAB_KEY_BINDINGS`` is the single source of truth for key documentation;
the README deliberately does not repeat it.
"""

from labgrid_tui.ui.format import CAPABILITY_ABBREV

TAB_KEY_BINDINGS = """\
labgrid-tui is fully keyboard-driven; vim/fzf muscle memory (`h` `j` `k` `l`,
`g`/`G`, `ctrl+d`/`ctrl+u`, `ctrl+n`/`ctrl+p`, `:`) works as aliases.

### movement (vim), in the table

| Keys | Does |
| --- | --- |
| arrows / `j` `k` | move |
| `g` / `G` | top / bottom |
| `h` / `l` | scroll table left/right |
| `ctrl+d` / `ctrl+u` | half page down/up |
| `pageup` / `pagedown` | page down/up |
| `ctrl+f` / `ctrl+b` | page down/up (alias) |

### actions

| Key | Does |
| --- | --- |
| `/` | filter |
| `enter` / `d` | detail view |
| `c` | commands |
| `p` | power commands |
| `r` | acquire |
| `shift+r` | release |
| `space` | mark |
| `ctrl+a` | toggle mark all visible |
| `a` | activity log |
| `y` | copy |
| `escape` | clear filter / clear marks |
| `tab` / `shift+tab` | cycle focus between the table and the activity log |
| `shift+p` | coordinators: switch, create, edit, delete |

### overlays

- **commands**: `left`/`right`/`tab`/`shift+tab` switch tabs, `ctrl+n`/
  `ctrl+p` move the list cursor, `enter`/`y` copy, `ctrl+e` edit before
  copy, `shift+enter` copy via select, `q`/`c`/`esc` close
- **detail**: `enter`/`y` copy details, `shift+enter` copy via select, `c`
  commands, `j`/`k`/arrows/`pageup`/`pagedown`/`g`/`G` scroll, `:` or
  `ctrl+p` palette, `esc`/`d` close
- **this help**: `left`/`right`/`h`/`l` switch tabs, `j`/`k`/arrows/
  `pageup`/`pagedown`/`g`/`G` scroll, `:` or `ctrl+p` palette,
  `escape`/`?`/`q` close
- **coordinators** (`shift+p`): `j`/`k`/arrows move, `enter` switch, `n`
  new, `e` edit, `x` delete (confirm modal), `esc`/`q` close

### palette

| Key | Does |
| --- | --- |
| `:` | command palette (actions, place, copy) |
| `ctrl+p` | command palette, same as `:` |

### quit

| Key | Does |
| --- | --- |
| `q` | quit |
| `ctrl+c` | quit |
"""

_CAPABILITY_ROWS = "\n".join(
    f"| `{abbrev}` | {cap} |" for cap, abbrev in sorted(CAPABILITY_ABBREV.items())
)

TAB_TABLE_REFERENCE = f"""\
### capability chips

green = online, red = offline, `?` = unknown class.

| Abbrev | Capability |
| --- | --- |
{_CAPABILITY_ROWS}

### status dots (S column)

| Dot | Meaning |
| --- | --- |
| green | free: no acquisition or reservation, and a resource matched |
| black | reserved: a reservation is queued or holding the place |
| blue | acquired: the place is held by a user |
| red | offline: nothing usable exported, unmatched, or every match offline |

### table columns

| Column | Meaning |
| --- | --- |
| m | mark indicator, filled when the row is marked |
| name | labgrid place name |
| s | status dot, see status dots above |
| capabilities | capability chips, one per resource class (see above) |
| user | who acquired the place, "-" if free |
| tags | the place's set-tags pairs, as labgrid-client shows them |
| changed | time since the place last changed state |
| comment | place comment / description |

Narrow terminals drop columns lowest-priority first (comment, tags,
changed, then user) to keep m/name/s/capabilities visible. Tags are cut
short rather than dropped for as long as the column fits, and `h`/`l`
scroll whatever remains.
"""

TAB_OPERATIONS = """\
### operations

| Operation | Does |
| --- | --- |
| mark then verb | `space` marks rows across refresh; `r`/`shift+r` act on marks, else cursor row |
| filter | `/` matches name, comment, tags, capability abbreviations, case-insensitive |
| get a bench | `r` copies one line that reserves, waits for the allocation and acquires |
| release | `shift+r` releases the bench; the reservation behind it lapses on its own |
| tour | `labgrid-tui tour` walks through this dashboard on fake data, no coordinator needed |

### what you can and cannot do, and why

Every bench lists every group of commands; what you cannot do right now is
greyed with the reason instead of hidden:

| Reason | Meaning |
| --- | --- |
| hold the bench first | resource commands need the bench acquired by you |
| held by USER | someone else acquired it; `r` reads "Queue and acquire" |
| reserved by USER | someone else's reservation is allocated to it |
| resource offline | the resource this command uses is not available |
| already yours | you hold it; nothing to acquire |
| all resources offline | nothing to acquire on a dead exporter |

The Manage tab carries the `r` line ("Acquire", or "Queue and acquire" on a
busy bench), the bare `acquire` ("Acquire now (no queue)"), Release, Allow
user and "Reserve (queue)". The `r` line is
`labgrid-client -p +$(labgrid-client reserve --wait --shell name=PLACE | cut -d= -f2) acquire`:
`reserve --wait --shell` prints `export LG_TOKEN=...` once the coordinator
allocates the bench and `-p +TOKEN` acquires that bench; it works the same
in bash, zsh and fish. After `release`, the reservation lapses by itself.

The Reservations tab (own tab in the command overlay, own entries in the
palette; absent if you have no reservations) lists your reservations:
"Cancel reservation TOKEN" always runs, "Acquire allocated place +TOKEN"
only once the coordinator allocates a place to it.

### labgrid concepts

- **place**: a named test seat (a device slot) on the coordinator
- **resource**: something an exporter offers a place (serial port, power...)
- **acquire**: lock a place for yourself: `labgrid-client -p PLACE acquire`
- **allow**: share your acquired place: `allow HOST/USER`
- **reservation**: queue for a place by tag filters: `reserve KEY=VALUE`

### managing places (labgrid-tui shows places; it never edits them)

A red dot with "-" capabilities means no exporter resource matches the place
yet. Places are managed with labgrid-client, all with `-p PLACE`:

| Command | Does |
| --- | --- |
| create | register a new, empty place |
| add-match EXPORTER/GROUP/CLS | attach resources by pattern (`*` allowed) |
| add-named-match PATTERN NAME | same, but name the resource |
| del-match PATTERN | detach resources |
| set-tags KEY=VALUE ... | tags (empty value deletes a tag) |
| set-comment TEXT | free-text comment |
| add-alias NAME / del-alias NAME | alternative names |
| delete | remove the place |

The coordinator has no accounts or roles: anyone who can reach its port can
run these. Labs usually declare places from configuration management
instead of editing them by hand.

Every action shown here is a real labgrid-client command line. Copy it, run
it, learn it.

### config

Optional file: `~/.config/labgrid-tui/config.toml` (`$XDG_CONFIG_HOME`
honoured). Coordinator address resolves `-x`/`--coordinator` flag, then
`LG_COORDINATOR`, then coordinators.toml's active coordinator, then the
`coordinator` key in config.toml, then `127.0.0.1:20408`. `-x` and
config.toml's `coordinator` key each also accept a name from
coordinators.toml instead of a literal address.

| Command | Does |
| --- | --- |
| `labgrid-tui config path` | print the config file path |
| `labgrid-tui config show` | print resolved coordinator, prefix, and counts |
| `labgrid-tui config init` | create a commented template config file |

### command packs

A command pack is a team-shared TOML file of extra command recipes,
registered explicitly with `labgrid-tui pack add PATH|URL`: nothing is
auto-discovered. Each pack gets its own tab (named after the pack) in the
command overlay and its own group in the palette. Pack entries are
always **copy-only**: `enter`/`y` copies, the TUI never runs one, no
matter what a pack file says.

A recipe's command line can use `{place}`, `{coordinator}`, `{prefix}`,
`{token}` (your reservation token for this place, if any), `{tag.KEY}`,
and `{res.CLASS.PARAM}` (a matched resource's param, or `.name`). An
entry whose placeholders can't resolve, or whose `requires` isn't met,
shows greyed out with a short reason instead of being hidden.

| Command | Does |
| --- | --- |
| `labgrid-tui pack add PATH-or-URL [--name N]` | validate and register a pack |
| `labgrid-tui pack list` | list registered packs, entry counts, load errors |
| `labgrid-tui pack show NAME` | show one pack's entries |
| `labgrid-tui pack update [NAME]` | re-fetch URL-sourced packs |
| `labgrid-tui pack remove NAME` | unregister a pack |

See the README (Command packs) for the full file format.

### coordinators

Named coordinators live in a separate, machine-owned file:
`~/.config/labgrid-tui/coordinators.toml` (`$XDG_CONFIG_HOME` honoured).
Manage them with `shift+p` in the TUI (switch/create/edit/delete) or the
CLI:

| Command | Does |
| --- | --- |
| `labgrid-tui coordinator list` | list configured coordinators |
| `labgrid-tui coordinator add NAME ADDRESS` | add one (`--prefix`, `--use`) |
| `labgrid-tui coordinator use NAME` | switch the active coordinator |
| `labgrid-tui coordinator remove NAME` | remove one (`--force` if active) |
| `labgrid-tui coordinator show [NAME]` | show one, or the active one |
| `labgrid-tui coordinator edit NAME` | edit one (`--address`, `--prefix`/`--no-prefix`) |
"""

TAB_ACTIVITY_LOG = """\
### activity log events

| Icon | Event |
| --- | --- |
| ▶ | acquired: a place was acquired |
| ■ | released: a place was released |
| ▲ | online: a resource came online |
| ▼ | offline: a resource went offline |
| ✖ | deleted: a place or resource was removed from the coordinator |
| ◆ | reservation: a reservation appeared, changed state, or disappeared |

Plain lines (no icon) are command output: copy/run/exit lines, undecorated.
A red line with no icon is a run command that exited non-zero.
"""
