"""Persisted UI convenience state: theme, panel visibility, first-run marker.

Machine-local state under XDG_STATE_HOME; losing it must never matter, so
every read and write failure degrades to defaults / a no-op. tomllib has
no writer, so the four flat keys are emitted by hand.
"""

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass
class UiState:
    theme: str | None = None
    show_activity: bool = False
    onboarded: bool = False


def state_path(env: Mapping[str, str]) -> Path:
    base = env.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "labgrid-tui" / "ui.toml"


def _bool(data: dict[str, object], key: str) -> bool:
    value = data.get(key, False)
    return value if isinstance(value, bool) else False


def load_state(path: Path) -> UiState:
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return UiState()
    theme = data.get("theme")
    return UiState(
        theme=theme if isinstance(theme, str) else None,
        show_activity=_bool(data, "show_activity"),
        onboarded=_bool(data, "onboarded"),
    )


def _dump(state: UiState) -> str:
    lines = []
    if state.theme is not None:
        lines.append(f'theme = "{state.theme}"')
    lines.append(f"show_activity = {str(state.show_activity).lower()}")
    lines.append(f"onboarded = {str(state.onboarded).lower()}")
    return "\n".join(lines) + "\n"


def save_state(path: Path, state: UiState) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_dump(state), encoding="utf-8")
    except OSError:
        pass
