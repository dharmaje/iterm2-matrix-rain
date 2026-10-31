"""Built-in checks that run without iTerm2: python -m matrix_rain.selftest"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

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


def skip(name: str, why: str) -> bool:
    """A check that cannot run here; it says so rather than passing silently."""
    print(f"SKIP  {name} ({why})")
    return True


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


def last_err(r) -> str:
    """The last line a subprocess wrote to stderr, or "" if it wrote none."""
    return (r.stderr.strip().splitlines() or [""])[-1]


def test_installer() -> bool:
    """Shell-level checks of install.sh: macOS preflight with stubbed iTerm2, sw_vers and PlistBuddy, then Linux."""
    import plistlib
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
        stub = tmp / "bin"
        stub.mkdir()

        def tool(name, body):
            (stub / name).write_text("#!/bin/sh\n" + body + "\n")
            (stub / name).chmod(0o755)

        tool("defaults", 'echo "$STUB_API"')
        tool("sw_vers", "echo 26.1")
        tool("osacompile", "exit 0")
        tool("osascript", "exit 0")
        # PlistBuddy is an absolute path in install.sh; MATRIX_RAIN_PLISTBUDDY points it here.
        tool("plistbuddy", f"exec '{sys.executable}' -c 'import plistlib, sys; "
                           "print(plistlib.load(open(sys.argv[1], \"rb\"))[\"CFBundleShortVersionString\"])' \"$3\"")
        base = dict(os.environ, PATH=f"{stub}:{os.environ['PATH']}",
                    MATRIX_RAIN_ITERM_APP=str(tmp / "iTerm.app"),
                    MATRIX_RAIN_PLISTBUDDY=str(stub / "plistbuddy"))
        base.pop("PYTHON", None)

        for ver, api, want_rc, want_msg, label in (
                ("3.7.6", "1", 0, "", "passes"),
                ("3.7.6", "0", 1, "Enable Python API", "API off aborts"),
                ("3.4.1", "1", 1, "upgrade iTerm2", "old iTerm2 aborts")):
            with open(app / "Info.plist", "wb") as fh:
                plistlib.dump({"CFBundleShortVersionString": ver}, fh)
            env = dict(base, STUB_API=api, MATRIX_RAIN_UNAME="Darwin")
            r = subprocess.run(["bash", "-c", f"{lib}; preflight"], capture_output=True, text=True, env=env)
            good = r.returncode == want_rc and want_msg in last_err(r) and "macOS " in r.stdout
            ok &= check(f"installer preflight (macOS) iTerm2 {ver}, API {api}: {label}", good,
                        f"rc {r.returncode} {last_err(r)[:60]}")

        osrel = tmp / "os-release"
        osrel.write_text('NAME="Ubuntu"\nVERSION_ID="26.04"\nID=ubuntu\n')
        tool("py-old", 'echo 3.9.1')
        tool("py-novenv", f'case "$*" in *ensurepip*) exit 1;; esac; exec "{sys.executable}" "$@"')
        linux = dict(base, MATRIX_RAIN_UNAME="Linux", MATRIX_RAIN_OS_RELEASE=str(osrel))
        for py, want_rc, want_msg, label in (
                (sys.executable, 0, "", "passes"),
                (str(stub / "py-novenv"), 1, "python3-venv", "no venv module aborts"),
                (str(stub / "py-old"), 1, "Python 3.10 or newer", "old python aborts"),
                (str(tmp / "no-such-python"), 1, "Python 3.10 or newer", "missing python aborts")):
            r = subprocess.run(["bash", "-c", f"{lib}; preflight"], capture_output=True, text=True,
                               env=dict(linux, PYTHON=py))
            good = r.returncode == want_rc and want_msg in last_err(r)
            if want_rc == 0:
                good &= "Ubuntu 26.04" in r.stdout and "iTerm2" not in r.stdout
            ok &= check(f"installer preflight (Linux): {label}", good, f"rc {r.returncode} {last_err(r)[:60]}")

        for flag, word in (("--autolaunch", "--autolaunch is macOS only"), ("--camera", "--camera is macOS only")):
            r = subprocess.run(["bash", str(REPO / "install.sh"), flag], capture_output=True, text=True,
                               env=dict(linux, HOME=str(tmp / "home")))
            ok &= check(f"installer {flag} on Linux says macOS only and exits 0",
                        r.returncode == 0 and word in r.stdout and not (tmp / "home").exists(),
                        f"rc {r.returncode}")

        home = tmp / "home2"
        (home / ".local" / "bin").mkdir(parents=True)
        link = home / ".local" / "bin" / "matrix-rain"
        venv_cmd = REPO / ".venv" / "bin" / "matrix-rain"
        r = subprocess.run(["bash", "-c", f"{lib}; link_command; link_command"], capture_output=True, text=True,
                           env=dict(linux, HOME=str(home)))
        ok &= check("installer links ~/.local/bin/matrix-rain to .venv (twice is fine)",
                    r.returncode == 0 and link.is_symlink() and os.readlink(link) == str(venv_cmd), f"rc {r.returncode}")
        link.unlink()
        link.write_text("mine\n")
        r = subprocess.run(["bash", "-c", f"{lib}; link_command"], capture_output=True, text=True,
                           env=dict(linux, HOME=str(home)))
        ok &= check("installer leaves a real file at ~/.local/bin/matrix-rain alone",
                    r.returncode == 0 and link.read_text() == "mine\n" and "not a link" in r.stderr, f"rc {r.returncode}")
        # Another install's link (pipx, uv tool) stays too.
        link.unlink()
        other = home / "pipx-matrix-rain"
        other.write_text("pipx\n")
        link.symlink_to(other)
        r = subprocess.run(["bash", "-c", f"{lib}; link_command"], capture_output=True, text=True,
                           env=dict(linux, HOME=str(home)))
        ok &= check("installer leaves another install's link alone",
                    r.returncode == 0 and os.readlink(link) == str(other) and "links to" in r.stderr, f"rc {r.returncode}")

        r = subprocess.run(["bash", "-c", f"{lib}; os_name; MATRIX_RAIN_UNAME=Plan9 os_name"],
                           capture_output=True, text=True, env=base)
        ok &= check("installer os_name honours MATRIX_RAIN_UNAME",
                    r.stdout.split() == [os.uname().sysname, "Plan9"], " ".join(r.stdout.split()))
    return ok


def test_platform() -> bool:
    import tempfile

    from . import platform as plat

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        rel = Path(tmp) / "os-release"
        rel.write_text('# comment\nNAME="Ubuntu"\nVERSION_ID="26.04"\nPRETTY_NAME=\'Ubuntu 26.04 LTS\'\n')
        for env, sp, release, want, label in (
                ({"TERM_PROGRAM": "iTerm.app"}, "darwin", None, ("iterm2", "macOS"), "macOS in iTerm2"),
                ({"ITERM_SESSION_ID": "w0t0p0:ABC"}, "darwin", None, ("iterm2", "macOS"), "macOS, iTerm2 via tmux"),
                ({"TERM_PROGRAM": "Apple_Terminal"}, "darwin", None, ("terminal", "macOS"), "macOS in Terminal.app"),
                ({"TERM_PROGRAM": "iTerm.app"}, "linux", rel, ("terminal", "Ubuntu"), "Linux ignores TERM_PROGRAM"),
                ({}, "linux", Path(tmp) / "missing", ("terminal", "Linux"), "Linux without os-release"),
                ({}, "freebsd14", None, ("terminal", "freebsd14"), "other OS")):
            d = plat.detect(env=env, sys_platform=sp, release=release)
            good = (d["backend"], d["os"]) == want and set(d) == {"backend", "os", "os_version", "reason"} and d["reason"]
            ok &= check(f"platform.detect: {label}", bool(good), f"{d['backend']} {d['os']} {d['os_version']}")
        d = plat.detect(env={}, sys_platform="linux", release=rel)
        ok &= check("platform.detect reads VERSION_ID", d["os_version"] == "26.04", d["os_version"])
        ok &= check("platform.os_release strips both quote styles",
                    plat.os_release(rel).get("PRETTY_NAME") == "Ubuntu 26.04 LTS")
    return ok


def test_notify() -> bool:
    import tempfile

    from . import versions

    ok = True
    if sys.platform == "darwin":
        return skip("notify uses notify-send", "Linux only")
    with tempfile.TemporaryDirectory() as tmp:
        stub = Path(tmp) / "bin"
        stub.mkdir()
        out = Path(tmp) / "args"
        (stub / "notify-send").write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{out}"\n')
        (stub / "notify-send").chmod(0o755)
        old = os.environ["PATH"]
        try:
            os.environ["PATH"] = str(stub)
            sent = versions.notify("-x \"hi\"", title="T")
            args = out.read_text().splitlines() if out.exists() else []
            ok &= check("notify uses notify-send on Linux, message after --",
                        sent and args == ["--app-name=T", "--", "T", '-x "hi"'], repr(args))
            os.environ["PATH"] = str(Path(tmp) / "empty")
            ok &= check("notify without notify-send returns False", versions.notify("x") is False)
        finally:
            os.environ["PATH"] = old
    return ok


def test_camera(cfg) -> bool:
    import tempfile

    from .camera import CameraSettings, Heat, MotionField, SyntheticSource, Tint

    ok = True
    st = CameraSettings.from_cfg(cfg)
    ok &= check("camera off by default", st.enabled is False)

    # Mask: a moving silhouette shows up, a still scene does not.
    src = SyntheticSource()
    field = MotionField(st)
    for i in range(25):
        field.feed(src.frame_at(3.0 + i / 10))
    moving = field.coverage()
    still = MotionField(st)
    frame = src.frame_at(2.0)
    for _ in range(15):
        still.feed(frame)
    ok &= check("mask: moving silhouette detected", 0.02 < moving < 0.6, f"{moving * 100:.1f}% of frame")
    ok &= check("mask: still scene is empty", still.coverage() == 0.0, f"{still.coverage() * 100:.2f}%")
    corner = field.mask.getpixel((2, 2))
    ok &= check("mask: far corner stays clear", corner == 0, f"value {corner}")

    # CameraSource.read() hands out each frame once (stub frames, no camera).
    from .camera import CameraSource

    cam = CameraSource()
    ok &= check("camera read: nothing before the first frame", cam.read() is None)
    cam._store(320, 240, 320, bytes([10]) * (320 * 240), 100.0)
    first = cam.read()
    again = cam.read()
    cam._store(320, 240, 320, bytes([90]) * (320 * 240), 100.1)
    second = cam.read()
    ok &= check("camera read: a new frame is returned once",
                first is not None and again is None and second is not None and cam.read() is None)
    ok &= check("camera read: arrival time and content follow the frame",
                cam.last_time == 100.1 and second.getpixel((5, 5)) == 90 and first.getpixel((5, 5)) == 10)
    # A camera that ignores the requested rate (30 fps) still feeds at most 10.
    fast = CameraSource(fps=10)
    for k in range(30):
        fast._store(320, 240, 320, bytes([k]) * (320 * 240), 200.0 + k / 30)
    st30 = fast.stats()
    ok &= check("camera at 30 fps is cut to the configured 10", 9 <= st30["delivered"] <= 11
                and st30["dropped"] >= 19, f"{st30['delivered']} kept, {st30['dropped']} dropped")

    # Warm-up gate: no mask (so no heat) for the first `warmup` seconds.
    gate = MotionField(st)
    syn2 = SyntheticSource()
    masks = []
    for i in range(25):
        gate.feed(syn2.frame_at(i / 10), 0.1)
        masks.append(gate.mask is not None)
    first_on = masks.index(True) if True in masks else None
    ok &= check(f"warm-up gate holds tint for {st.warmup:g} s", first_on is not None
                and abs(first_on / 10 - st.warmup) <= 0.15 and gate.coverage() > 0,
                f"first tint at {first_on / 10 if first_on is not None else None} s")
    hw = Heat(8, 6, st.fade)
    cold = MotionField(st)
    cold.feed(Image.new("L", (320, 240), 0), 0.1)
    cold.feed(Image.new("L", (320, 240), 255), 0.1)   # huge change right after opening
    hw.update(cold.mask, 0.1)
    ok &= check("opening flash adds no heat during warm-up", cold.coverage() > 0.9 and hw.peak() == 0,
                f"{cold.coverage() * 100:.0f}% raw motion, heat {hw.peak()}")

    syn = SyntheticSource(fps=10, clock=lambda: 50.0)
    syn.open()
    ok &= check("synthetic read: once per frame interval", syn.read() is not None and syn.read() is None)

    # Real-camera conditions: a few gray levels of noise everywhere must stay
    # below the threshold; a waving hand tens of levels off must cross it.
    import random as _r

    rng = _r.Random(3)
    base = bytes(rng.randrange(90, 120) for _ in range(320 * 240))
    def noisy(shift_box=None, delta=0):
        img = Image.frombytes("L", (320, 240), bytes(min(255, max(0, b + rng.randrange(-3, 4))) for b in base))
        if shift_box:
            img.paste(Image.eval(img.crop(shift_box), lambda v: min(255, v + delta)), shift_box[:2])
        return img
    f = MotionField(st)
    for _ in range(8):
        f.feed(noisy(), 0.1)
    noise_cover = f.coverage()
    f.feed(noisy((120, 60, 200, 160), 30), 0.1)
    wave_cover = f.coverage()
    ok &= check(f"real-frame noise below threshold {f.threshold}", noise_cover == 0.0, f"{noise_cover * 100:.2f}%")
    ok &= check("waving region (+30 levels) detected", wave_cover > 0.05, f"{wave_cover * 100:.1f}%")
    ok &= check("frame-to-frame difference is measured", f.last_frame_diff > 1.0, f"{f.last_frame_diff:.2f} levels")

    # Heat rises at once and fades to zero over `fade` seconds.
    heat = Heat(8, 6, fade=1.5)
    full = Image.new("L", (320, 240), 255)
    heat.update(full, 0.1)
    ok &= check("heat rises on first motion frame", heat.peak() == 255)
    t, alive_at_1_2 = 0.0, None
    while t < 2.0:
        heat.update(None, 0.1)
        t = round(t + 0.1, 3)
        if t == 1.2:
            alive_at_1_2 = heat.peak()
    gone_at = None
    heat2 = Heat(8, 6, fade=1.5)
    heat2.update(full, 0.1)
    for k in range(1, 30):
        heat2.update(None, 0.1)
        if heat2.peak() == 0:
            gone_at = k / 10
            break
    ok &= check("heat still visible at 1.2 s", (alive_at_1_2 or 0) > 0, f"{alive_at_1_2}")
    ok &= check("heat gone by 1.5 s (fade)", gone_at is not None and 1.4 <= gone_at <= 1.6, f"{gone_at} s")

    # fade_curve: half-faded heat is much dimmer than fresh heat, so new motion stands out.
    from .camera import shape_heat
    half = Image.new("L", (8, 6), 128)
    shaped = shape_heat(half, 2.5).getextrema()[1]
    ok &= check("fade_curve 2.5 dims half-faded heat below 25%", shaped < 64, f"{shaped}/255")
    ok &= check("fade_curve keeps fresh heat at full", shape_heat(Image.new("L", (8, 6), 255), 2.5).getextrema()[1] == 255)
    ok &= check("fade_curve 1 is linear", shape_heat(half, 1.0).getextrema()[1] == 128)

    # Idle clear: with the fast idle fade, heat is gone within about 0.3 s.
    h3 = Heat(8, 6, fade=5.0)
    h3.update(full, 0.1)
    steps = 0
    while h3.peak() > 0 and steps < 50:
        h3.update(None, 0.05, fade=0.3)
        steps += 1
    ok &= check("idle_fade clears a 5 s heat in about 0.3 s", 4 <= steps <= 8, f"{steps * 0.05:.2f} s")
    st_gain = CameraSettings.from_cfg({"camera": {"gain": 1.5, "burst_strength": 1.7}})
    g0 = Tint.from_settings(Image.new("L", (8, 6), 255), st_gain, 0.0).gain
    g1 = Tint.from_settings(Image.new("L", (8, 6), 255), st_gain, 1.0).gain
    ok &= check("gain 1.5 applies without a burst", abs(g0 - 1.5) < 1e-6, f"{g0}")
    ok &= check("burst multiplies the gain", abs(g1 - 1.5 * 1.7) < 1e-6, f"{g1}")

    from .camera import outline
    blob_mask = Image.new("L", (320, 240), 0)
    ImageDraw.Draw(blob_mask).ellipse((100, 60, 220, 200), fill=255)
    thin = outline(blob_mask, 1).point(lambda v: 255 if v > 127 else 0).histogram()[255]
    thick = outline(blob_mask, 3).point(lambda v: 255 if v > 127 else 0).histogram()[255]
    ok &= check("outline_width 3 is thicker than 1", thick > 2 * thin, f"{thin} vs {thick} px")
    ok &= check("heat dilates one cell", _dilation_ok())

    # Tint: every channel stays between the plain and the fully gold frame.
    w, h = frame_size(40, 12, cfg["daemon"])
    sm = StateModel(cfg)
    p = sm.palettes["working"]

    def fresh():
        r = Renderer(w, h, cfg["render"], seed=5)
        for _ in range(12):
            r.step(1 / 6, p)
        return r

    r = fresh()
    plain = r.draw(p)
    hot = Image.new("L", (r.ncols, r.nrows), 255)
    gold = r.draw(p, Tint(hot, st.tint, st.head_tint, 1.0))
    half = r.draw(p, Tint(hot, st.tint, st.head_tint, 0.5))
    a, b, c = plain.tobytes(), gold.tobytes(), half.tobytes()
    inside = all(min(x, y) - 1 <= z <= max(x, y) + 1 for x, y, z in zip(a, b, c))
    ok &= check("tint lerp stays between plain and gold", inside)
    ok &= check("full tint changes the frame", a != b)
    filled = fresh().draw(p, Tint(hot, st.tint, st.head_tint, 1.0, 0.4))
    lit_plain = sum(1 for v in plain.convert("L").tobytes() if v > 20)
    lit_filled = sum(1 for v in filled.convert("L").tobytes() if v > 20)
    ok &= check("fill lights moving cells where no rain falls", lit_filled > lit_plain * 1.5,
                f"{lit_plain} -> {lit_filled} lit pixels")
    import dataclasses

    sil = SyntheticSource()
    fm = MotionField(dataclasses.replace(st, shape="fill"))
    hs = Heat(40, 30, st.fade)
    for i in range(26):
        fm.feed(sil.frame_at(1.5 + i / 10), 0.1)
        hs.update(fm.mask, 0.1)
    inside = sil.figure_mask(4.0).transpose(Image.Transpose.FLIP_LEFT_RIGHT).resize((40, 30), Image.Resampling.BOX)
    pairs = list(zip(inside.tobytes(), hs.img.tobytes()))
    core = [h for m, h in pairs if m == 255]
    ok &= check("synthetic figure interior stays hot", core and min(core) > 200, f"min heat {min(core) if core else None}")
    # Outline mode: one frame's mask is the edge of the moving figure.
    of = MotionField(st)                                   # default shape = "outline"
    ff = MotionField(dataclasses.replace(st, shape="fill"))
    for i in range(26):
        frame = sil.frame_at(1.5 + i / 10)
        of.feed(frame, 0.1)
        ff.feed(frame, 0.1)
    grid = (80, 60)
    ocell = of.mask.resize(grid, Image.Resampling.BOX).point(lambda v: 255 if v > 40 else 0)
    fcell = ff.mask.resize(grid, Image.Resampling.BOX).point(lambda v: 255 if v > 40 else 0)
    ocells, fcells = ocell.histogram()[255], fcell.histogram()[255]
    ok &= check("outline sets far fewer cells than fill", 0 < ocells < fcells * 0.5, f"{ocells} vs {fcells} cells")
    obox, fbox = ocell.getbbox(), fcell.getbbox()
    ok &= check("outline surrounds the figure", obox is not None and fbox is not None
                and all(abs(x - y) <= 2 for x, y in zip(obox, fbox)), f"{obox} vs {fbox}")
    core = fcell.filter(ImageFilter.MinFilter(9))
    core_n = core.histogram()[255]
    hollow = sum(1 for c, o in zip(core.tobytes(), ocell.tobytes()) if c == 255 and o == 255)
    ok &= check("outline leaves the figure's interior dark", core_n > 0 and hollow <= core_n * 0.1,
                f"{hollow} of {core_n} interior cells lit")

    # Burst: stronger tint for the first 3 s of motion after a rest, then eases back.
    from .camera import Burst

    b = Burst(burst=3.0, rest=2.0)
    for t10 in range(0, 100):          # continuous motion from t = 0 to 10 s
        b.update(0.05, t10 / 10)
    ok &= check("burst starts on first motion", b.start == 0.0 and b.factor(1.0) == 1.0 and b.factor(2.9) == 1.0)
    ok &= check("burst eases back after 3 s", 0.0 < b.factor(3.5) < 1.0 and b.factor(4.1) == 0.0)
    ok &= check("continuous motion starts no second burst", b.start == 0.0 and b.factor(9.9) == 0.0)
    b.update(0.05, 11.0)               # 1.1 s pause: shorter than burst_rest
    ok &= check("short pause starts no burst", b.start == 0.0)
    b.update(0.05, 13.5)               # 2.5 s pause: a new burst
    ok &= check("motion after 2 s rest bursts again", b.start == 13.5 and b.factor(14.0) == 1.0)
    full_b = Tint.from_settings(hot, st, 1.0)
    none_b = Tint.from_settings(hot, st, 0.0)
    ok &= check("burst tint: stronger at start, normal after", abs(full_b.gain - st.gain * st.burst_strength) < 1e-6 and full_b.strength == 1.0
                and full_b.head == st.burst_head_tint and abs(none_b.gain - st.gain) < 1e-6 and none_b.tail == st.tint)
    rb = fresh()
    plain_gold = rb.draw(p, none_b).convert("L").tobytes()
    burst_gold = fresh().draw(p, full_b).convert("L").tobytes()
    ok &= check("burst frame is brighter", sum(burst_gold) > sum(plain_gold) * 1.1,
                f"{sum(burst_gold) / max(1, sum(plain_gold)):.2f}x")

    zero = Image.new("L", (r.ncols, r.nrows), 0)
    ok &= check("no heat is byte-identical to camera off",
                fresh().draw(p).tobytes() == fresh().draw(p, Tint(zero, st.tint, st.head_tint, 1.0)).tobytes()
                and a == fresh().draw(p, None).tobytes())

    # camera enable/disable writes only [camera] enabled and keeps the rest.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.toml"
        config.set_key("camera", "enabled", True, path)
        ok &= check("camera enable creates the file", config.tomllib.loads(path.read_text()) == {"camera": {"enabled": True}})
        path.write_text("# keep me\n[daemon]\nfps = 4  # slow\n\n[camera]\nfade = 2.0\nenabled = true\n\n[states.idle]\nspeed = 3\n")
        config.set_key("camera", "enabled", False, path)
        text = path.read_text()
        data = config.tomllib.loads(text)
        ok &= check("camera disable keeps other keys and comments",
                    data == {"daemon": {"fps": 4}, "camera": {"fade": 2.0, "enabled": False}, "states": {"idle": {"speed": 3}}}
                    and "# keep me" in text and "# slow" in text)
        path.write_text("[daemon]\nfps = 4\n")
        config.set_key("camera", "enabled", True, path)
        ok &= check("camera enable adds a section", config.tomllib.loads(path.read_text())["camera"] == {"enabled": True})
    return ok


def test_profile_component() -> bool:
    """The camera toggle baked into profiles/Matrix.json matches the daemon's component."""
    import json
    from pathlib import Path

    import importlib.util

    from .statusbar import CLASS, REQUEST_KEY, registration_b64

    if importlib.util.find_spec("iterm2") is None:
        return skip("profile toggle matches the daemon's registration", "macOS only, needs the iterm2 package")
    prof = json.loads((Path(__file__).resolve().parent.parent / "profiles" / "Matrix.json").read_text())
    comps = prof["Profiles"][0]["Status Bar Layout"]["components"]
    entry = next((c for c in comps if c.get("class") == CLASS), None)
    ok = check("profile carries the camera toggle", entry is not None)
    if entry:
        ok &= check("profile toggle matches the daemon's registration (run matrix-rain status-component if not)",
                    entry["configuration"].get(REQUEST_KEY) == registration_b64())
    return ok


def test_toggle(cfg) -> bool:
    """The status bar toggle, with no iTerm2 and the camera stubbed by the synthetic source."""
    import asyncio
    import copy
    import tempfile

    from . import daemon as dmod

    ok = True
    with tempfile.TemporaryDirectory() as tmp:
        saved, saved_log = config.CONFIG_PATH, dmod.log
        dmod.log = lambda msg: None
        config.CONFIG_PATH = Path(tmp) / "config.toml"
        config.CONFIG_PATH.write_text("# mine\n[daemon]\nfps = 5\n")
        try:
            async def run():
                c = copy.deepcopy(cfg)
                d = dmod.Daemon(None, c, [], motion_demo=True)
                d.motion = None                      # start with the camera off
                d.cfg["camera"]["enabled"] = False
                checks = [("toggle shows off at start", d.status_text() == "○ camera off")]
                on = await d.toggle_camera("selftest")
                data = config.tomllib.loads(config.CONFIG_PATH.read_text())
                checks.append(("toggle on: camera state and config flip",
                               on and d.motion is not None and d.motion.source.name == "synthetic"
                               and data["camera"]["enabled"] is True and data["daemon"]["fps"] == 5))
                checks.append(("toggle on: status text", d.status_text() == "◉ camera demo"))
                d.motion.open()                      # as if the loop had opened it
                m = d.motion
                off = await d.toggle_camera("selftest")
                data = config.tomllib.loads(config.CONFIG_PATH.read_text())
                checks.append(("toggle off: source closed and config saved",
                               not off and d.motion is None and not m.is_open
                               and data["camera"]["enabled"] is False
                               and "# mine" in config.CONFIG_PATH.read_text()))
                checks.append(("toggle off: status text", d.status_text() == "○ camera off"))
                return checks
            for name, good in asyncio.run(run()):
                ok &= check(name, good)
        finally:
            config.CONFIG_PATH, dmod.log = saved, saved_log
    return ok


GOLDEN_FRAMES = {
    # sha256 of the RGB pixels of `matrix-rain preview` (160x45, warm-up 8 s,
    # seed 7, defaults) with Pillow's default font, from before the
    # simulation moved into sim.py.  These must never change.
    ("idle", False): "236397d5659172aefffb10d7eed2311063a6a2006cb9050e17d7003b0e07a660",
    ("working", False): "3b8fab00035117d5823a00d8c90fd3fed99e472704a01ed871ecc772015aa8e3",
    ("working", True): "a3ae56c048fd87b5c9c0a5d759883547248fe05870e2c4fcbdfae3005bbc42d3",
}
GOLDEN_PNGS = {   # the same frames as preview writes them (zlib may vary by build)
    ("idle", False): "f35533892e1a7fcf857b1a62a6c2022eac9de31fcc4803a6cba06d8698aa36b5",
    ("working", False): "5e46bce7865aaacb45a4bc4a0cb5b3799d11b6c1fbbbcf0e3a2c8b6a587bea64",
    ("working", True): "feffb3db0892570dfab1612901942cf3c14b7a9702b2114f80267e697c2798ed",
}


def test_golden_frames(cfg) -> bool:
    """preview frames are byte-identical to the ones from before the sim.py split."""
    import hashlib
    import io

    from .cli import _motion_demo, _sim

    ok = True
    for (state, motion), want in GOLDEN_FRAMES.items():
        label = f"golden frame {state}{' motion-demo' if motion else ''}"
        r, p, c = _sim(160, 45, state, 8.0, 6.0, cfg=cfg)
        if r.sprites.font_path != "pillow-default":
            ok &= skip(label, f"hashes are for Pillow's default font; this machine uses {Path(r.sprites.font_path).name}")
            continue
        img = r.draw(p, _motion_demo(r, c) if motion else None)
        got = hashlib.sha256(img.tobytes()).hexdigest()
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        png = "PNG identical too" if hashlib.sha256(buf.getvalue()).hexdigest() == GOLDEN_PNGS[(state, motion)] \
            else "PNG bytes differ (zlib build), pixels decide"
        ok &= check(label, got == want, png if got == want else f"sha256 {got[:16]}")
    return ok


def test_sim(cfg) -> bool:
    """sim.Rain on its own matches the renderer's simulation and needs no Pillow."""
    import random

    from .sim import Rain

    w, h = frame_size(80, 24, cfg["daemon"])
    r = Renderer(w, h, cfg["render"], seed=11)
    rain = Rain(r.ncols, r.nrows, len(r.sprites.glyphs), cfg["render"], random.Random(11))
    p = StateModel(cfg).palettes["working"]
    for _ in range(30):
        r.step(1 / 6, p)
        rain.step(1 / 6, p)
    ok = check("sim.Rain alone follows the renderer's random sequence",
               (rain.cells, rain.active, rain.head, rain.length) == (r.cells, r.active, r.head, r.length))
    r.resize(w // 2, h)
    rain.resize(r.ncols, r.nrows)
    r.step(1 / 6, p)
    rain.step(1 / 6, p)
    ok &= check("sim.Rain resize keeps the same sequence", rain.cells == r.cells and rain.head == r.head,
                f"{r.ncols}x{r.nrows}")
    res = subprocess.run([sys.executable, "-c", "import sys, matrix_rain.term, matrix_rain.cli; "
                          "print('PIL' in sys.modules)"], capture_output=True, text=True, cwd=str(REPO), timeout=20)
    ok &= check("term and the event path import no Pillow", res.stdout.strip() == "False",
                res.stdout.strip() or res.stderr.strip()[-60:])
    return ok


class _VT:
    """A tiny terminal model: accepts only the sequences term.py may send."""

    import re as _re
    TOKEN = _re.compile(r"\x1b\[(\?\d+[hl]|\d+;\d+H|\d+C|2J|[\d;]*m)|\x1b|[\x00-\x1f\x7f]|[^\x00-\x1f\x7f\x1b]")

    def __init__(self, cols, rows):
        self.cols, self.rows = cols, rows
        self.y = self.x = 0
        self.fg, self.bg, self.bold = None, None, False
        self.grid = {}
        self.errors: list[str] = []
        self.modes: dict = {}

    def feed(self, text: str) -> None:
        for m in self.TOKEN.finditer(text):
            tok, seq = m.group(0), m.group(1)
            if seq is None and (tok == "\x1b" or ord(tok) < 0x20 or tok == "\x7f"):
                self.errors.append(f"stray control {tok!r} at {m.start()}")
                continue
            if seq is None:
                if not (0 <= self.y < self.rows and 0 <= self.x < self.cols):
                    self.errors.append(f"write outside the screen at {self.y},{self.x}")
                elif tok == " ":
                    self.grid.pop((self.y, self.x), None)
                else:
                    self.grid[(self.y, self.x)] = (tok, self.fg, self.bold)
                self.x += 1
            elif seq.startswith("?"):
                self.modes[seq[1:-1]] = seq[-1]
            elif seq.endswith("H"):
                y, x = seq[:-1].split(";")
                self.y, self.x = int(y) - 1, int(x) - 1
            elif seq.endswith("C"):
                self.x += int(seq[:-1])
            elif seq == "2J":
                self.grid = {}
                self.screen_bg = self.bg
            else:
                self._sgr([int(v) for v in seq[:-1].split(";")] if seq[:-1] else [0])

    def _sgr(self, ps: list[int]) -> None:
        i = 0
        while i < len(ps):
            v = ps[i]
            if v == 0:
                self.fg, self.bg, self.bold = None, None, False
            elif v == 1:
                self.bold = True
            elif v == 22:
                self.bold = False
            elif v in (38, 48) and ps[i + 1:i + 2] == [2]:
                c = tuple(ps[i + 2:i + 5])
                if v == 38:
                    self.fg = c
                else:
                    self.bg = c
                i += 4
            elif v in (38, 48) and ps[i + 1:i + 2] == [5]:
                if v == 38:
                    self.fg = ps[i + 2]
                else:
                    self.bg = ps[i + 2]
                i += 2
            else:
                self.errors.append(f"unexpected SGR {v}")
            i += 1


def test_term(cfg) -> bool:
    """The text rain's encoder, glyphs, colors and settings, without a terminal."""
    import random
    import unicodedata

    from . import term
    from .sim import Rain

    ok = True
    kana = term.glyph_set("katakana")
    narrow = [c for c in kana if unicodedata.east_asian_width(c) not in ("H", "Na") or unicodedata.combining(c)]
    ok &= check("text glyphs are one cell wide (half-width katakana, no combining marks)", not narrow
                and any(0xFF66 <= ord(c) <= 0xFF9D for c in kana), f"{len(kana)} glyphs, bad: {narrow}")
    ascii_g = term.glyph_set("ascii")
    ok &= check("ascii glyphs are printable ASCII only", all(0x21 <= ord(c) <= 0x7E for c in ascii_g))
    cases = {(0, 0, 0): 16, (255, 255, 255): 231, (255, 0, 0): 196, (0, 255, 0): 46, (128, 128, 128): 244,
             (8, 8, 8): 232, (30, 165, 75): 35}
    got = {k: term.rgb_to_256(*k) for k in cases}
    ok &= check("256-color fallback picks the nearest cube or gray entry", got == cases,
                ", ".join(f"{k}->{v}" for k, v in got.items() if v != cases[k]))
    ok &= check("256-color indexes stay in 16-255", all(16 <= term.rgb_to_256(r, g, b) <= 255
                for r in range(0, 256, 17) for g in range(0, 256, 17) for b in range(0, 256, 17)))
    ok &= check("color auto: truecolor from COLORTERM", term.color_mode("auto", {"COLORTERM": "truecolor"}) == "truecolor"
                and term.color_mode("auto", {"COLORTERM": "24bit"}) == "truecolor"
                and term.color_mode("auto", {}) == "256" and term.color_mode("256", {"COLORTERM": "truecolor"}) == "256")

    class A:
        fps = color = glyphs = follow = None
        demo = False
    good = term.options(cfg, A())
    ok &= check("[terminal] defaults", good["fps"] == 20 and good["follow"] == "auto" and good["glyphs"] == "katakana",
                str(good))
    bad = 0
    for field, value in (("follow", "pts/3"), ("color", "16"), ("glyphs", "emoji"), ("fps", 500.0)):
        a = A()
        setattr(a, field, value)
        try:
            term.options(cfg, a)
        except ValueError:
            bad += 1
    a = A()
    a.follow = "tty:/dev/pts/3"
    ok &= check("bad term settings are refused, tty:/dev/pts/N accepted", bad == 4 and term.options(cfg, a)["follow"] == a.follow)

    # Encoder: feed every frame to a model terminal; it must match the cells.
    for mode in ("truecolor", "256"):
        clock = FakeClock()
        sm = StateModel(cfg, transition=1.0, clock=clock)
        sm.set("working")
        cols, rows = 64, 20
        rain = Rain(60, rows, len(kana), cfg["render"], random.Random(3))
        scr = term.Screen(cols, rows, rain.ncols, mode, kana, cfg["render"])
        vt = _VT(cols, rows)
        mismatch, repaints, sizes = 0, 0, []
        for i in range(60):
            if i == 20:
                sm.set("waiting")
            if i == 40:
                cols, rows = 50, 16
                rain.resize(cols, rows)
                scr.resize(cols, rows, rain.ncols)
                vt = _VT(cols, rows)
            clock.t += 0.05
            sm.tick()
            p = sm.current()
            rain.step(0.05, p)
            out = scr.frame(rain, p)
            repaints += "\x1b[2J" in out
            sizes.append(len(out.encode()))
            vt.feed(out)
            want = {}
            for idx, (g, rgb, bold) in scr.prev.items():
                want[divmod(idx, cols)] = (kana[g], rgb if mode == "truecolor" else term.rgb_to_256(*rgb), bold)
            mismatch += vt.grid != want
        ok &= check(f"text frames ({mode}) are well-formed and match the simulation",
                    not vt.errors and mismatch == 0 and repaints >= 3,
                    f"{len(vt.errors)} errors {vt.errors[:2]}, {mismatch} mismatched frames, {repaints} repaints")
        ok &= check(f"text frames ({mode}) redraw only changed cells", scr.frame(rain, p) == ""
                    and max(sizes[45:]) < sizes[40], f"steady {max(sizes[45:])} B vs full {sizes[40]} B")
    full = _VT(10, 3)
    full.feed(term.ENTER + term.LEAVE)
    ok &= check("leaving restores screen, cursor, wrap and sync",
                full.modes == {"1049": "l", "25": "h", "7": "h", "2026": "l"} and term.LEAVE.count("\x1b[0m") == 1)

    for follow, mine, tty, want in (("any", "/dev/pts/1", "/dev/pts/9", True), ("auto", None, None, True),
                                    ("auto", None, "/dev/pts/2", True), ("auto", "/dev/pts/1", "/dev/pts/1", True),
                                    ("auto", "/dev/pts/1", "/dev/pts/2", False), ("auto", "/dev/pts/1", None, False),
                                    ("tty:/dev/pts/4", None, "/dev/pts/4", True),
                                    ("tty:/dev/pts/4", None, "/dev/pts/5", False)):
        ok &= check(f"follow {follow} (mine {mine}) event from {tty}", term.accepts(follow, mine, tty) == want)
    tr = term.TermRain(cfg, follow="tty:/dev/pts/4")
    ok &= check("term applies only followed, known states",
                not tr.apply("waiting", "/dev/pts/5") and not tr.apply("bogus", "/dev/pts/4")
                and tr.apply("waiting", "/dev/pts/4") and tr.state.target == "waiting")
    return ok


def test_events() -> bool:
    """matrix-rain event fans out to every running term socket and skips dead ones."""
    import socket
    import tempfile

    from . import cli, term

    ok = True
    with tempfile.TemporaryDirectory(prefix="mr-") as tmp:
        saved = config.CACHE_DIR, config.SOCKET_PATH
        config.CACHE_DIR, config.SOCKET_PATH = Path(tmp), Path(tmp) / "events.sock"
        socks = []
        try:
            d = term.socket_dir()
            d.mkdir()
            for name in ("a", "b", "dead"):
                s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
                s.bind(str(d / f"{name}.sock"))
                s.settimeout(2.0)
                socks.append(s)
            socks.pop().close()                     # dead.sock stays behind, nothing bound
            sent = cli.send_event("waiting", "/dev/pts/7", None)
            got = [json.loads(s.recv(4096)) for s in socks]
            ok &= check("event reaches every term, no daemon needed", sent and all(
                g == {"state": "waiting", "tty": "/dev/pts/7", "session": None} for g in got), str(got))
            ok &= check("stale term socket is removed", term.clean_stale(d) == 1 and not (d / "dead.sock").exists()
                        and (d / "a.sock").exists())
            for _ in range(1200):                   # far past the queue length: EAGAIN is skipped
                cli.send_event("working", None, None)
            ok &= check("a full term queue never blocks or raises", True)
            for s in socks:
                s.close()
            (d / "a.sock").unlink()
            (d / "b.sock").unlink()
            ok &= check("event with no listener at all reports not delivered", cli.send_event("idle", None, None) is False)
            s, path = term.open_socket(d)
            socks = [s]
            ok &= check("term socket is private (0700 dir, 0600 socket)",
                        (d.stat().st_mode & 0o777) == 0o700 and (path.stat().st_mode & 0o777) == 0o600
                        and path.name == f"{os.getpid()}.sock")
            if (REPO / ".venv" / "bin" / "python").exists():
                s.settimeout(8.0)
                env = dict(os.environ, MATRIX_RAIN_CACHE=tmp)
                env.pop("TMUX", None)
                subprocess.run(["bash", str(HOOK)], input='{"hook_event_name":"Stop"}', capture_output=True,
                               text=True, env=env, timeout=5)
                try:
                    msg = json.loads(s.recv(4096))
                except OSError as exc:
                    msg = {"error": str(exc)}
                ok &= check("hook Stop reaches a term as done", msg.get("state") == "done", str(msg)[:70])
            else:
                ok &= skip("hook Stop reaches a term as done", "no .venv in this clone")
        finally:
            for s in socks:
                s.close()
            config.CACHE_DIR, config.SOCKET_PATH = saved
    return ok


def _pty_term(tmp: str, script, cols=80, rows=24, timeout=10.0):
    """Run `matrix-rain term` on a pty; script(ctl) drives it.  Returns (rc, output, attrs ok)."""
    import fcntl
    import pty
    import select
    import struct
    import termios
    import time

    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    before = termios.tcgetattr(slave)
    env = dict(os.environ, MATRIX_RAIN_CACHE=tmp, MATRIX_RAIN_CONFIG=str(Path(tmp) / "none.toml"),
               COLORTERM="truecolor", TERM="xterm-256color")
    env.pop("TMUX", None)
    proc = subprocess.Popen([sys.executable, "-m", "matrix_rain", "term"], stdin=slave, stdout=slave, stderr=slave,
                            env=env, cwd=str(REPO), start_new_session=True)
    os.close(slave)
    out = bytearray()

    def read_for(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            r, _, _ = select.select([master], [], [], 0.05)
            if r:
                try:
                    out.extend(os.read(master, 65536))
                except OSError:
                    return

    class Ctl:
        pass
    ctl = Ctl()
    ctl.proc, ctl.master, ctl.read_for, ctl.out = proc, master, read_for, out
    try:
        script(ctl)
        try:
            rc = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = None
        read_for(0.2)
        attrs = termios.tcgetattr(master)
        restored = attrs[3] & termios.ECHO == before[3] & termios.ECHO and attrs[3] & termios.ICANON == before[3] & termios.ICANON
    finally:
        os.close(master)
    return rc, out.decode("utf-8", "replace"), restored


def test_term_pty() -> bool:
    """matrix-rain term on a real pty: draws, follows events, resizes, exits cleanly."""
    import fcntl
    import re
    import signal
    import socket
    import struct
    import tempfile
    import termios
    import time

    from . import term

    if not hasattr(os, "openpty"):
        return skip("text rain on a pty", "no pty support")
    ok = True
    with tempfile.TemporaryDirectory(prefix="mr-") as tmp:
        d = Path(tmp) / "term"

        def drive(ctl):
            end = time.monotonic() + 5
            while time.monotonic() < end and not list(d.glob("*.sock")):
                ctl.read_for(0.05)
            ctl.read_for(0.6)
            ctl.mark = len(ctl.out)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            for path in d.glob("*.sock"):
                s.sendto(json.dumps({"state": "waiting", "tty": None, "session": None}).encode(), str(path))
            s.close()
            ctl.read_for(1.5)
            ctl.mark2 = len(ctl.out)
            fcntl.ioctl(ctl.master, termios.TIOCSWINSZ, struct.pack("HHHH", 15, 50, 0, 0))
            ctl.proc.send_signal(signal.SIGWINCH)
            ctl.read_for(0.5)
            ctl.sock_seen = bool(list(d.glob("*.sock")))
            ctl.proc.send_signal(signal.SIGTERM)

        holder = {}

        def script(ctl):
            holder["ctl"] = ctl
            drive(ctl)
        rc, text, restored = _pty_term(tmp, script)
        ctl = holder["ctl"]
        after_event = bytes(ctl.out[ctl.mark:ctl.mark2]).decode("utf-8", "replace")
        after_resize = bytes(ctl.out[ctl.mark2:]).decode("utf-8", "replace")
        vt = _VT(80, 24)
        vt.feed(bytes(ctl.out[:ctl.mark2]).decode("utf-8", "replace"))
        ok &= check("term on a pty: enters the alternate screen and draws katakana",
                    text.startswith(term.ENTER) and "\x1b[0;48;2;2;6;4m\x1b[2J" in text
                    and any(0xFF66 <= ord(ch) <= 0xFF9D for ch in text), f"{len(ctl.out)} bytes")
        ok &= check("term on a pty: no stray escape codes or writes off screen", not vt.errors, str(vt.errors[:2]))
        ok &= check("term on a pty: an event turns the rain amber", "48;2;7;5;2m\x1b[2J" in after_event)
        rows_used = [int(m) for m in re.findall(r"\x1b\[(\d+);\d+H", after_resize.split("\x1b[2J", 1)[-1])]
        ok &= check("term on a pty: resize repaints within the new size", "\x1b[2J" in after_resize
                    and rows_used and max(rows_used) <= 15, f"max row {max(rows_used) if rows_used else None}")
        ok &= check("term on a pty: SIGTERM restores the terminal and exits 0",
                    rc == 0 and text.endswith(term.LEAVE) and restored, f"rc {rc}, tail {text[-12:]!r}")
        ok &= check("term on a pty: its socket is removed on exit", ctl.sock_seen and not list(d.glob("*.sock")))

        def press_q(ctl):
            ctl.read_for(1.0)
            os.write(ctl.master, b"q")
        rc, text, restored = _pty_term(tmp, press_q)
        ok &= check("term on a pty: q quits cleanly", rc == 0 and text.endswith(term.LEAVE) and restored, f"rc {rc}")
    return ok


def _dilation_ok() -> bool:
    from .camera import Heat

    heat = Heat(10, 10, fade=1.5)
    mask = Image.new("L", (320, 240), 0)
    mask.paste(255, (160, 120, 192, 144))   # exactly one cell: column 5, row 5
    img = heat.update(mask, 0.0)
    return img.getpixel((5, 5)) == 255 and img.getpixel((4, 4)) == 255 and img.getpixel((6, 6)) == 255 \
        and img.getpixel((3, 5)) == 0 and img.getpixel((7, 5)) == 0


def main() -> int:
    cfg = config.load(Path("/nonexistent"))
    results = [test_renderer(cfg), test_golden_frames(cfg), test_sim(cfg), test_state(cfg), test_hook(), test_versions(),
               test_update(), test_installer(), test_platform(), test_notify(),
               test_camera(cfg), test_toggle(cfg), test_profile_component(),
               test_term(cfg), test_events(), test_term_pty()]
    ok = all(results)
    print("all checks passed" if ok else "some checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
