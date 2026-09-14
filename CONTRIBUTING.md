# Contributing

Before opening a PR, run the gate:

```
uv sync
uv run ruff check src tests
uv run mypy src
uv run pytest
```

Keep one topic per PR and one logical commit per change: pull requests
are rebase-merged, so squash fixups on the branch before merging.
Include a short rationale in the description: what problem it solves and
why this approach.

Automated review comments (CI bot, code review tools) are advisory only.
A human maintainer reviews and merges every change.

Two hard rules for this project:

- The coordinator client stays read only: no gRPC mutations, and never
  `PollReservation` (it refreshes a reservation timeout, a write in disguise).
- Command pack entries are copy only; the TUI never executes them.
