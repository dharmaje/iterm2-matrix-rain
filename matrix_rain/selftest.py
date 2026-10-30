"""Built-in checks that run without iTerm2: python -m matrix_rain.selftest"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageFilter

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
    ok &= check("burst tint: stronger at start, normal after", full_b.gain == st.burst_strength and full_b.strength == 1.0
                and full_b.head == st.burst_head_tint and none_b.gain == 1.0 and none_b.tail == st.tint)
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
    results = [test_renderer(cfg), test_state(cfg), test_hook(), test_versions(), test_update(), test_installer(), test_camera(cfg), test_toggle(cfg)]
    ok = all(results)
    print("all checks passed" if ok else "some checks FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
