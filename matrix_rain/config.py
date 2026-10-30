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


def set_key(section: str, key: str, value: bool, path: Path | None = None) -> Path:
    """Write one boolean key into the user's TOML, keeping everything else.

    Edits the text in place: replaces the key's line inside [section], or adds
    the key under an existing [section] header, or appends a new section.
    Comments, ordering and other keys stay as they were.
    """
    import re

    path = path or CONFIG_PATH
    literal = "true" if value else "false"
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines(keepends=True)
    header = re.compile(r"^\s*\[\s*" + re.escape(section) + r"\s*\]\s*(#.*)?$")
    any_header = re.compile(r"^\s*\[")
    keyline = re.compile(r"^(\s*)" + re.escape(key) + r"\s*=\s*[^#\n]*?(\s*#.*)?$")
    start = next((i for i, ln in enumerate(lines) if header.match(ln.rstrip("\n"))), None)
    if start is None:
        if text and not text.endswith("\n"):
            text += "\n"
        text += ("\n" if text else "") + f"[{section}]\n{key} = {literal}\n"
    else:
        end = next((j for j in range(start + 1, len(lines)) if any_header.match(lines[j])), len(lines))
        for j in range(start + 1, end):
            m = keyline.match(lines[j].rstrip("\n"))
            if m:
                lines[j] = f"{m.group(1)}{key} = {literal}{m.group(2) or ''}\n"
                break
        else:
            lines.insert(start + 1, f"{key} = {literal}\n")
        text = "".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Validate before writing so a bad merge never replaces a good file.
    tomllib.loads(text)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)
    return path
