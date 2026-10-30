"""Built-in checks that run without iTerm2: python -m matrix_rain.selftest"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

from . import config
from .render import Renderer, frame_size
from .state import StateModel

REPO = Path(__file__).resolve().parent.parent
HOOK = REPO / "hooks" / "claude-code-hook.sh"

HOOK_CASES = [
    ({"hook_event_name": "SessionStart"}, "idle"),
    ({"hook_event_name": "UserPromptSubmit"}, "working"),
    ({"hook_event_name": "PreToolUse", "tool_name": "Bash"}, "working"),
    ({"hook_event_name": "PostToolUse"}, "working"),
    ({"hook_event_name": "Notification", "notification_type": "permission_prompt"}, "waiting"),
    ({"hook_event_name": "Notification", "notification_type": "idle_prompt"}, "waiting"),
    ({"hook_event_name": "Notification", "notification_type": "auth_success"}, ""),
    ({"hook_event_name": "PermissionRequest"}, "waiting"),
    ({"hook_event_name": "PostToolUseFailure"}, "error"),
    ({"hook_event_name": "Stop"}, "done"),
    ({"hook_event_name": "SubagentStop"}, "done"),
    ({"hook_event_name": "StopFailure"}, "error"),
    ({"hook_event_name": "SessionEnd"}, "off"),
    ({"hook_event_name": "PreCompact"}, ""),
]


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")
    return ok


def test_renderer(cfg) -> bool:
    ok = True
    for cols, rows in ((80, 24), (209, 51), (400, 100)):
        w, h = frame_size(cols, rows, cfg["daemon"])
        r = Renderer(w, h, cfg["render"], seed=1)
        sm = StateModel(cfg)
        sm.set("working")
        p = sm.current()
        for _ in range(12):
            r.step(1 / 6, p)
        img = r.draw(p)
        ok &= check(f"frame size {cols}x{rows}", img.size == (w, h) and w <= 1200 and img.mode == "RGB",
                    f"{img.size}")
        hi = max(band[1] for band in img.getextrema())
        mean = sum(img.convert("L").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)) for _ in [0])
        ok &= check(f"frame is soft {cols}x{rows}", hi < 250 and mean < 60, f"max channel {hi}, mean {mean}")
    return ok


def test_state(cfg) -> bool:
    ok = True
    clock = FakeClock()
    sm = StateModel(cfg, transition=1.0, clock=clock)
    pal = sm.palettes
    sequence = ["working", "error", "working", "waiting", "done", "off", "idle"]
    in_range = True
    for name in sequence:
        sm.set(name)
        for _ in range(60):
            clock.t += 0.1
            sm.tick()
            p = sm.current()
            for f in ("tail", "head", "background"):
                vals = getattr(p, f)
                lo = [min(getattr(q, f)[i] for q in pal.values()) for i in range(3)]
                hi = [max(getattr(q, f)[i] for q in pal.values()) for i in range(3)]
                in_range &= all(lo[i] - 1e-6 <= vals[i] <= hi[i] + 1e-6 for i in range(3))
            for f in ("speed", "density", "brightness", "mutation"):
                v = getattr(p, f)
                in_range &= min(getattr(q, f) for q in pal.values()) - 1e-6 <= v <= max(getattr(q, f) for q in pal.values()) + 1e-6
    ok &= check("interpolation stays in range", in_range)

    # Midway through a transition the value sits between the two palettes.
    sm = StateModel(cfg, transition=1.0, clock=clock)
    sm.set("waiting")
    clock.t += 0.5
    mid = sm.current().tail[0]
    a, b = pal["idle"].tail[0], pal["waiting"].tail[0]
    ok &= check("no pop at transition start", min(a, b) < mid < max(a, b), f"{a} -> {mid:.1f} -> {b}")

    # Error holds, then eases back to the previous state.
    sm = StateModel(cfg, transition=1.0, clock=clock)
    sm.set("working")
    sm.set("error")
    sm.set("working")  # arrives during the hold; must not cut it short
    clock.t += 2.0
    sm.tick()
    ok &= check("error holds through new events", sm.target == "error")
    clock.t += 2.5
    sm.tick()
    ok &= check("error returns to previous state", sm.target == "working")

    sm = StateModel(cfg, transition=1.0, clock=clock)
    sm.set("done")
    clock.t += 4.5
    sm.tick()
    ok &= check("done returns to idle", sm.target == "idle")
    return ok


def test_hook() -> bool:
    ok = True
    for force_sed in ("0", "1"):
        for payload, want in HOOK_CASES:
            env = dict(os.environ, MATRIX_RAIN_HOOK_DRY_RUN="1", MATRIX_RAIN_HOOK_NO_JQ=force_sed)
            res = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), capture_output=True,
                                 text=True, env=env, timeout=5)
            got = res.stderr.strip().removeprefix("state=")
            label = payload["hook_event_name"] + ("/" + payload["notification_type"] if "notification_type" in payload else "")
            ok &= check(f"hook {'sed' if force_sed == '1' else 'jq '} {label}",
                        got == want and res.stdout == "" and res.returncode == 0,
                        f"-> {got or '(none)'}")
    return ok


def test_versions() -> bool:
    import tempfile

    from . import versions

    ok = True
    for raw, want in (("3.7.6", (3, 7)), ("3.7.6beta1", (3, 7)), ("3.10", (3, 10)),
                      ("4.0.0-rc2", (4, 0)), ("", None), ("junk", None)):
        ok &= check(f"major.minor of {raw!r}", versions.major_minor(raw) == want, str(versions.major_minor(raw)))
    for installed, want in (("3.7.6", "ok"), ("3.7.99beta1", "ok"), ("3.8.0", "newer"), ("4.0", "newer"),
                            ("3.5.0", "ok"), ("3.4.20", "too-old"), (None, "unknown")):
        got = versions.compare(installed, built="3.7", minimum="3.5")
        ok &= check(f"iTerm2 {installed} vs built 3.7", got == want, got)
    # A newer iTerm2 toasts once per major.minor, not on every start.
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "iTerm.app"
        (app / "Contents").mkdir(parents=True)
        import plistlib
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": "9.1.2beta3"}, fh)
        toasts: list[str] = []
        logs: list[str] = []
        for _ in range(3):
            versions.check_iterm2(logs.append, toasts.append, cache_dir=Path(tmp) / "c", app=app)
        ok &= check("newer iTerm2 toasts once", len(toasts) == 1 and len(logs) == 3, f"{len(toasts)} toast(s)")
        with open(app / "Contents" / "Info.plist", "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": "3.4.1"}, fh)
        ok &= check("iTerm2 below minimum refuses", versions.check_iterm2(logs.append, toasts.append,
                    cache_dir=Path(tmp) / "c", app=app) is False)
    return ok


def test_update() -> bool:
    import shutil
    import tempfile

    from . import update

    if shutil.which("git") is None:
        return check("update decision (git not installed, skipped)", True)

    def git(cwd, *args):
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, env=env)

    def commit(repo, name, text):
        (Path(repo) / name).write_text(text)
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", name)

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        origin, upstream, clone = tmp / "origin.git", tmp / "upstream", tmp / "clone"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
        subprocess.run(["git", "clone", "-q", str(origin), str(upstream)], check=True, capture_output=True)
        git(upstream, "checkout", "-q", "-b", "main")
        commit(upstream, "a.txt", "one\n")
        git(upstream, "push", "-q", "origin", "main")
        subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True, capture_output=True)

        d = update.evaluate(clone)
        ok &= check("update: current when equal", d.action == "current", d.reason)

        commit(upstream, "b.txt", "two\n")
        git(upstream, "push", "-q", "origin", "main")
        d = update.evaluate(clone)
        ok &= check("update: clean clone behind origin updates", d.action == "update", d.reason)

        (clone / "a.txt").write_text("local edit\n")
        d = update.evaluate(clone)
        ok &= check("update: dirty clone refuses", d.action == "refuse", d.reason)
        git(clone, "checkout", "-q", "--", "a.txt")

        git(clone, "checkout", "-q", "-b", "side")
        d = update.evaluate(clone)
        ok &= check("update: other branch refuses", d.action == "refuse", d.reason)
        git(clone, "checkout", "-q", "main")

        logs: list[str] = []
        toasts: list[str] = []
        did = update.run(logs.append, toasts.append, force=True, check_only=True, repo=clone, cache_dir=tmp / "c")
        ok &= check("update: check_only notifies without changing HEAD",
                    not did and len(toasts) == 1 and not (clone / "b.txt").exists())
        did = update.run(logs.append, toasts.append, force=True, check_only=False, repo=clone, cache_dir=tmp / "c")
        ok &= check("update: fast-forward applied", did and (clone / "b.txt").exists(), logs[-1])
        did = update.run(logs.append, toasts.append, force=False, check_only=False, repo=clone, cache_dir=tmp / "c")
        ok &= check("update: skipped within the hour", not did and logs[-1].startswith("update: skipped"))

        commit(upstream, "c.txt", "three\n")
        git(upstream, "push", "-q", "origin", "main")
        commit(clone, "local.txt", "mine\n")
        d = update.evaluate(clone)
        ok &= check("update: diverged clone refuses", d.action == "refuse", d.reason)

        git(clone, "remote", "remove", "origin")
        d = update.evaluate(clone)
        ok &= check("update: no origin skips", d.action == "skip", d.reason)
    return ok


def test_installer() -> bool:
    """Shell-level checks of install.sh with stubbed iTerm2 and defaults."""
    import tempfile

    ok = True
    lib = f'MATRIX_RAIN_INSTALL_LIB=1 source "{REPO / "install.sh"}"'
    cases = [("3.7.6", "3.5", 0), ("3.7.6beta1", "3.7", 0), ("3.4.20", "3.5", 1), ("3.10", "3.9", 0),
             ("3.10.0", "3.10", 0), ("2.9", "3.5", 1), ("1.0.0", "1.0.1", 1)]
    for found, need, rc in cases:
        r = subprocess.run(["bash", "-c", f'{lib}; ver_ge "{found}" "{need}"'], capture_output=True, text=True)
        ok &= check(f"installer ver_ge {found} >= {need}", r.returncode == rc, f"rc {r.returncode}")
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        app = tmp / "iTerm.app" / "Contents"
        app.mkdir(parents=True)
        import plistlib
        stub = tmp / "bin"
        stub.mkdir()
        (stub / "defaults").write_text('#!/bin/sh\necho "$STUB_API"\n')
        (stub / "defaults").chmod(0o755)
        for ver, api, want_rc, label in (("3.7.6", "1", 0, "passes"), ("3.7.6", "0", 1, "API off aborts"),
                                         ("3.4.1", "1", 1, "old iTerm2 aborts")):
            with open(app / "Info.plist", "wb") as fh:
                plistlib.dump({"CFBundleShortVersionString": ver}, fh)
            env = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}", STUB_API=api,
                       MATRIX_RAIN_ITERM_APP=str(tmp / "iTerm.app"))
            r = subprocess.run(["bash", "-c", f"{lib}; preflight"], capture_output=True, text=True, env=env)
            ok &= check(f"installer preflight iTerm2 {ver}, API {api}: {label}", r.returncode == want_rc,
                        (r.stderr.strip().splitlines() or [""])[-1][:70])
    return ok


def main() -> int:
    cfg = config.load(Path("/nonexistent"))
    results = [test_renderer(cfg), test_state(cfg), test_hook(), test_versions(), test_update(), test_installer()]
    ok = all(results)
    print("all checks passed" if ok else "some checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
