"""Which backend to run here: the iTerm2 daemon or the terminal (text) rain."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

OS_RELEASE = Path(os.environ.get("MATRIX_RAIN_OS_RELEASE", "/etc/os-release"))


def os_release(path: Path | None = None) -> dict[str, str]:
    """KEY=value pairs from an os-release file, quotes stripped.  {} when unreadable."""
    out: dict[str, str] = {}
    try:
        text = (path or OS_RELEASE).read_text()
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if not sep or key.startswith("#"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _macos_version() -> str:
    try:
        r = subprocess.run(["sw_vers", "-productVersion"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def detect(env: dict | None = None, sys_platform: str | None = None,
           release: Path | None = None) -> dict[str, str]:
    """{"backend", "os", "os_version", "reason"}.  backend is "iterm2" or "terminal".

    iTerm2 only on macOS inside iTerm2 (TERM_PROGRAM=iTerm.app, or
    ITERM_SESSION_ID set, which survives into tmux).  Everything else gets the
    text rain.  The arguments exist for selftest; never raises.
    """
    env = os.environ if env is None else env
    plat = sys.platform if sys_platform is None else sys_platform
    if plat == "darwin":
        os_name, os_version = "macOS", _macos_version()
        if env.get("TERM_PROGRAM") == "iTerm.app":
            return {"backend": "iterm2", "os": os_name, "os_version": os_version,
                    "reason": "macOS and TERM_PROGRAM=iTerm.app"}
        if env.get("ITERM_SESSION_ID"):
            return {"backend": "iterm2", "os": os_name, "os_version": os_version,
                    "reason": "macOS and ITERM_SESSION_ID is set"}
        term = env.get("TERM_PROGRAM") or "an unknown terminal"
        return {"backend": "terminal", "os": os_name, "os_version": os_version,
                "reason": f"macOS but not iTerm2 ({term})"}
    if plat.startswith("linux"):
        info = os_release(release)
        os_name = info.get("NAME") or "Linux"
        os_version = info.get("VERSION_ID") or info.get("VERSION") or ""
        return {"backend": "terminal", "os": os_name, "os_version": os_version,
                "reason": "not macOS, so iTerm2 is not available"}
    return {"backend": "terminal", "os": plat, "os_version": "",
            "reason": "not macOS, so iTerm2 is not available"}
