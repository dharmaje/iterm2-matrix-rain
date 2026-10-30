"""Self-update: fast-forward a clean clone from its origin remote.

Runs before the daemon connects to iTerm2.  Every git call is captured, so
nothing reaches the terminal, and any failure is one log line followed by
carrying on with the current code.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from . import config

REPO = Path(__file__).resolve().parent.parent
STAMP = "last-update-check"
NOTIFIED = "update-available-notified"
FETCH_TIMEOUT = 20


@dataclass
class Decision:
    action: str   # "update", "current", "refuse", "skip"
    reason: str
    branch: str = ""
    target: str = ""


def _git(repo: Path, *args: str, timeout: float = 10) -> subprocess.CompletedProcess:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="true", LC_ALL="C")
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          timeout=timeout, env=env, stdin=subprocess.DEVNULL)


def _out(repo: Path, *args: str) -> str:
    r = _git(repo, *args)
    return r.stdout.strip() if r.returncode == 0 else ""


def evaluate(repo: Path = REPO, fetch: bool = True) -> Decision:
    """Decide whether a fast-forward update should happen.  Never raises."""
    try:
        if shutil.which("git") is None:
            return Decision("skip", "git is not on PATH")
        top = _out(repo, "rev-parse", "--show-toplevel")
        if not top or Path(top).resolve() != Path(repo).resolve():
            return Decision("skip", f"{repo} is not the root of a git clone")
        if not _out(repo, "remote", "get-url", "origin"):
            return Decision("skip", "no origin remote")
        if fetch:
            try:
                r = _git(repo, "fetch", "--quiet", "origin", timeout=FETCH_TIMEOUT)
            except subprocess.TimeoutExpired:
                return Decision("skip", f"git fetch timed out after {FETCH_TIMEOUT} s")
            if r.returncode != 0:
                return Decision("skip", "git fetch failed: " + (r.stderr.strip().splitlines() or ["?"])[-1])
        ref = _out(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
        branch = ref.rsplit("/", 1)[-1] if ref else "main"
        upstream = f"origin/{branch}"
        target = _out(repo, "rev-parse", "--verify", "--quiet", upstream)
        if not target:
            return Decision("skip", f"{upstream} not found", branch)
        head = _out(repo, "rev-parse", "HEAD")
        if head == target:
            return Decision("current", "up to date", branch, target)
        if _git(repo, "merge-base", "--is-ancestor", upstream, "HEAD").returncode == 0:
            return Decision("current", f"local {branch} is ahead of {upstream}", branch, target)
        if _out(repo, "status", "--porcelain"):
            return Decision("refuse", "working tree has local changes", branch, target)
        if _out(repo, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
            return Decision("refuse", f"HEAD is not on {branch}", branch, target)
        if _git(repo, "merge-base", "--is-ancestor", "HEAD", upstream).returncode != 0:
            return Decision("refuse", f"local {branch} has diverged from {upstream}", branch, target)
        return Decision("update", f"fast-forward to {target[:7]}", branch, target)
    except Exception as exc:  # noqa: BLE001
        return Decision("skip", f"update check failed: {exc}")


def _version_at(repo: Path) -> str:
    try:
        text = (repo / "matrix_rain" / "__init__.py").read_text()
        m = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        return m.group(1) if m else ""
    except OSError:
        return ""


def apply(repo: Path, d: Decision, python: str = sys.executable) -> tuple[bool, str]:
    """Fast-forward and refresh dependencies.  Returns (ok, label of the new code)."""
    before = _version_at(repo)
    r = _git(repo, "merge", "--ff-only", "--quiet", f"origin/{d.branch}", timeout=30)
    if r.returncode != 0:
        return False, "fast-forward failed: " + (r.stderr.strip().splitlines() or ["?"])[-1]
    req = repo / "requirements.txt"
    if req.exists():
        p = subprocess.run([python, "-m", "pip", "install", "-q", "-r", str(req)],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
        if p.returncode != 0:
            return False, "code updated but pip install failed: " + (p.stderr.strip().splitlines() or ["?"])[-1]
    after = _version_at(repo)
    label = after if after and after != before else d.target[:7]
    return True, label


def run(log, notifier, force: bool = False, check_only: bool | None = None,
        repo: Path = REPO, cache_dir: Path | None = None) -> bool:
    """The whole update step.  Returns True when new code was installed (re-exec)."""
    cfg = config.load().get("update", {})
    if not force and not cfg.get("auto", True):
        return False
    if check_only is None:
        check_only = bool(cfg.get("check_only", False))
    cache = cache_dir or config.CACHE_DIR
    stamp = cache / STAMP
    interval = float(cfg.get("min_interval", 3600))
    if not force:
        try:
            age = time.time() - stamp.stat().st_mtime
            if age < interval:
                log(f"update: skipped, last check {age / 60:.0f} min ago (use --update-now to force)")
                return False
        except OSError:
            pass
    try:
        cache.mkdir(parents=True, exist_ok=True)
        stamp.touch()
    except OSError:
        pass
    d = evaluate(repo)
    if d.action != "update":
        log(f"update: {d.action}, {d.reason}")
        return False
    if check_only:
        marker = cache / NOTIFIED
        try:
            seen = marker.read_text().strip()
        except OSError:
            seen = ""
        log(f"update: available ({d.reason}); check_only is set")
        if seen != d.target:
            notifier(f"An iterm2-matrix-rain update is available ({d.target[:7]}).")
            try:
                marker.write_text(d.target + "\n")
            except OSError:
                pass
        return False
    ok, label = apply(repo, d)
    if not ok:
        log(f"update: {label}; continuing with the current code")
        return False
    log(f"update: updated to {label}")
    notifier(f"iterm2-matrix-rain updated to {label}")
    return True
