# Contributing

Before opening a PR, run the gate:

```
uv sync
uv run ruff check src tests
uv run mypy src
uv run pytest
```

Keep one topic per PR. Include a short rationale in the description:
what problem it solves and why this approach.

Automated review comments (CI bot, code review tools) are advisory only.
A human maintainer reviews and merges every change.

Two hard rules for this project:

- The coordinator client stays read only: no gRPC mutations, ever.
- Command pack entries are copy only; the TUI never executes them.
