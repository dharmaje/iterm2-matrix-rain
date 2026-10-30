"""Configuration: packaged defaults merged with the user's TOML file."""

from __future__ import annotations

import copy
import os
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULTS_PATH = Path(__file__).with_name("defaults.toml")
CONFIG_PATH = Path(
    os.environ.get("MATRIX_RAIN_CONFIG", "~/.config/iterm2-matrix-rain/config.toml")
).expanduser()
CACHE_DIR = Path(os.environ.get("MATRIX_RAIN_CACHE", "~/.cache/iterm2-matrix-rain")).expanduser()
SOCKET_PATH = CACHE_DIR / "events.sock"

STATES = ("idle", "working", "waiting", "error", "done", "off")


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path: Path | None = None) -> dict:
    """Return the effective configuration."""
    with open(DEFAULTS_PATH, "rb") as fh:
        cfg = tomllib.load(fh)
    user = path or CONFIG_PATH
    if user.exists():
        with open(user, "rb") as fh:
            cfg = _merge(cfg, tomllib.load(fh))
    for name in STATES:
        if name not in cfg.get("states", {}):
            raise ValueError(f"config is missing [states.{name}]")
    return cfg
