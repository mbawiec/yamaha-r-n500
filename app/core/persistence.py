"""Simple JSON state file — currently used for future Speaker A/B IR state tracking."""
import json
from pathlib import Path

_STATE_PATH = Path(__file__).parent.parent.parent / "app" / "core" / "states.json"

_DEFAULTS = {
    "speaker_a": "On",
    "speaker_b": "On",
}


def load() -> dict:
    if _STATE_PATH.exists():
        try:
            with open(_STATE_PATH) as f:
                return {**_DEFAULTS, **json.load(f)}
        except (json.JSONDecodeError, OSError):
            pass
    return dict(_DEFAULTS)


def save(state: dict) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w") as f:
        json.dump(state, f)
