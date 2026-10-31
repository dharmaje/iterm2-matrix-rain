"""iTerm2 version checks and desktop notifications (macOS or Linux)."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import BUILT_FOR_ITERM2, MIN_ITERM2, config

ITERM_APP = Path(os.environ.get("MATRIX_RAIN_ITERM_APP", "/Applications/iTerm.app"))
WARNED_FILE = "warned-iterm2-version"


def major_minor(version: str | None) -> tuple[int, int] | None:
    """'3.7.6beta1' -> (3, 7).  Patch level and suffixes are ignored."""
    if not version:
        return None
    m = re.match(r"\s*(\d+)\.(\d+)", str(version))
    return (int(m.group(1)), int(m.group(2))) if m else None


def installed_iterm2(app: Path | None = None) -> str | None:
    try:
        with open((app or ITERM_APP) / "Contents" / "Info.plist", "rb") as fh:
            return plistlib.load(fh).get("CFBundleShortVersionString")
    except Exception:
        return None


def compare(installed: str | None, built: str = BUILT_FOR_ITERM2,
            minimum: str = MIN_ITERM2) -> str:
    """'unknown', 'too-old', 'newer' or 'ok'."""
    have = major_minor(installed)
    if have is None:
        return "unknown"
    if have < major_minor(minimum):
        return "too-old"
    if have > major_minor(built):
        return "newer"
    return "ok"


def notify(message: str, title: str = "iterm2-matrix-rain") -> bool:
    """Post one desktop notification: osascript on macOS, notify-send elsewhere.

    False when there is no way to notify (no notify-send) or it failed.  Never raises.
    """
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        if sys.platform == "darwin":
            cmd = ["/usr/bin/osascript", "-e",
                   f"display notification {q(message)} with title {q(title)}"]
        else:
            tool = shutil.which("notify-send")
            if not tool:
                return False
            cmd = [tool, f"--app-name={title}", "--", title, message]
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


def check_iterm2(log, notifier=notify, cache_dir: Path | None = None, app: Path | None = None) -> bool:
    """Log the comparison; toast once per newer major.minor.  False means too old to run."""
    installed = installed_iterm2(app)
    verdict = compare(installed)
    log(f"iTerm2 {installed or 'not found'}; built for {BUILT_FOR_ITERM2}, minimum {MIN_ITERM2}: {verdict}")
    if verdict == "too-old":
        log(f"iTerm2 {installed} is older than {MIN_ITERM2}; update iTerm2.  Exiting.")
        return False
    if verdict == "newer":
        mm = "%d.%d" % major_minor(installed)
        cache = cache_dir or config.CACHE_DIR
        marker = cache / WARNED_FILE
        try:
            already = marker.read_text().strip() == mm
        except OSError:
            already = False
        if not already:
            notifier(f"iTerm2 {mm} is newer than the {BUILT_FOR_ITERM2} this was built for; "
                     "check for an update if the rain misbehaves.")
            try:
                cache.mkdir(parents=True, exist_ok=True)
                marker.write_text(mm + "\n")
            except OSError:
                pass
    return True
