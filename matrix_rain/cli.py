"""Command line entry point: matrix-rain <command>."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

HELP = f"""\
matrix-rain: soft animated digital rain behind iTerm2 text, driven by Claude Code hooks.

Commands:
  daemon     Animate opted-in iTerm2 sessions (profile named "Matrix" by default).
  event      Tell the daemon this terminal's state changed (idle, working, waiting,
             error, done, off).  Always exits 0, even when the daemon is not running.
  preview    Render one frame to a PNG without iTerm2.
  bench      Measure render and PNG encode time per frame.
  selftest   Run the built-in checks (no iTerm2 needed).
  camera     Optional camera motion tint: enable, disable, status, test.
  update     Fast-forward this clone from origin now (clean clone only), then exit.
  version    Print this project's version, the iTerm2 version it was built for,
             and the installed iTerm2 version.

Examples:
  matrix-rain daemon
  matrix-rain daemon --session <session-id>
  matrix-rain event working
  matrix-rain event --tty /dev/ttys003 waiting
  matrix-rain preview docs/preview-working.png --state working
  matrix-rain bench --cols 209 --rows 51
  matrix-rain selftest
  matrix-rain preview docs/preview-motion.png --state working --motion-demo
  matrix-rain bench --motion
  matrix-rain camera enable
  matrix-rain camera test
  matrix-rain update
  matrix-rain version
  matrix-rain status-component     # the camera toggle entry for the profile's status bar

Run "matrix-rain <command> -h" for a command's options.
"""

VENV_HINT = f"""\
matrix-rain must run from its virtual environment.  Create it with:
  cd {REPO}
  ./install.sh --install
or by hand:
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  .venv/bin/pip install -e .
then run .venv/bin/matrix-rain (or activate the venv first).
"""


def in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


# ---------------------------------------------------------------- event

def _tmux_client_tty(env: dict) -> str | None:
    if not env.get("TMUX"):
        return None
    cmd = ["tmux", "display-message", "-p"]
    if env.get("TMUX_PANE"):
        cmd += ["-t", env["TMUX_PANE"]]
    cmd.append("#{client_tty}")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=0.3, env=env)
        tty = out.stdout.strip()
        return tty if tty.startswith("/dev/") else None
    except Exception:
        return None


def _own_tty() -> str | None:
    for fd in (0, 1, 2):
        try:
            if os.isatty(fd):
                return os.ttyname(fd)
        except OSError:
            pass
    try:
        fd = os.open("/dev/tty", os.O_RDONLY | os.O_NOCTTY)
        try:
            return os.ttyname(fd)
        finally:
            os.close(fd)
    except OSError:
        return None


def resolve_target(env: dict | None = None) -> dict:
    """Work out which iTerm2 session this process belongs to.

    Inside tmux, $ITERM_SESSION_ID is the environment of whoever started the
    tmux server and may name another window, so the tmux client tty is used.
    """
    env = dict(os.environ if env is None else env)
    if env.get("TMUX"):
        return {"tty": _tmux_client_tty(env), "session": None}
    sid = env.get("ITERM_SESSION_ID", "")
    session = sid.split(":", 1)[1] if ":" in sid else (sid or None)
    return {"tty": _own_tty(), "session": session}


def send_event(state: str, tty: str | None, session: str | None) -> bool:
    from .config import SOCKET_PATH

    msg = json.dumps({"state": state, "tty": tty, "session": session}).encode()
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        s.setblocking(False)
        try:
            s.sendto(msg, str(SOCKET_PATH))
        finally:
            s.close()
        return True
    except OSError:
        return False


def cmd_event(args) -> int:
    try:
        target = {"tty": args.tty, "session": args.session}
        if not args.tty and not args.session:
            target = resolve_target()
        ok = send_event(args.state, target["tty"], target["session"])
        if args.verbose:
            print(f"state={args.state} tty={target['tty']} session={target['session']} "
                  f"delivered={ok}", file=sys.stderr)
    except Exception as exc:  # never fail a hook
        if args.verbose:
            print(f"event not sent: {exc}", file=sys.stderr)
    return 0


# ---------------------------------------------------------------- preview / bench

def _sim(cols, rows, state, seconds, fps, seed=7):
    from . import config
    from .render import Renderer, frame_size
    from .state import StateModel

    cfg = config.load()
    w, h = frame_size(cols, rows, cfg["daemon"])
    r = Renderer(w, h, cfg["render"], seed=seed)
    p = StateModel(cfg).palettes[state]
    for _ in range(int(seconds * fps)):
        r.step(1.0 / fps, p)
    return r, p, cfg


def _motion_demo(r, cfg, seconds: float = 1.0, fps: float = 10.0, t_end: float = 10.0):
    """Run the synthetic silhouette through the motion pipeline; return a Tint."""
    from .camera import Burst, CameraSettings, Heat, MotionField, SyntheticSource, Tint

    s = CameraSettings.from_cfg(cfg)
    src = SyntheticSource()
    motion = MotionField(s)
    heat = Heat(r.ncols, r.nrows, s.fade, dilate=s.shape == "fill")
    burst = Burst(s.burst, s.burst_rest)
    seconds += s.warmup   # frames inside the warm-up gate add no heat
    steps = int(seconds * fps)
    for i in range(steps + 1):
        t = t_end - seconds + i / fps
        motion.feed(src.frame_at(t), 1.0 / fps)
        if motion.mask is not None:
            burst.update(motion.coverage(), t)
        heat.update(motion.mask, 1.0 / fps)
    return Tint.from_settings(heat.img, s, burst.factor(t))


def cmd_preview(args) -> int:
    r, p, cfg = _sim(args.cols, args.rows, args.state, args.warmup, 6.0)
    tint = _motion_demo(r, cfg) if args.motion_demo else None
    img = r.draw(p, tint)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    print(f"wrote {out} ({img.width}x{img.height}, state {args.state}, font {r.sprites.font_path})")
    return 0


def cmd_bench(args) -> int:
    import io
    import statistics

    from . import config
    from .render import Renderer, SpriteCache, frame_size

    cfg = config.load()
    t0 = time.perf_counter()
    sprites = SpriteCache(cfg["render"])
    t_sprites = (time.perf_counter() - t0) * 1000
    r, p, cfg = _sim(args.cols, args.rows, args.state, 8, 6.0)
    w, h = r.width, r.height
    step_ms, draw_ms, enc_ms = [], [], []
    motion = heat = src = tint_cfg = None
    if args.motion:
        from .camera import CameraSettings, Heat, MotionField, SyntheticSource, Tint

        cs = CameraSettings.from_cfg(cfg)
        src, motion, heat = SyntheticSource(), MotionField(cs), Heat(r.ncols, r.nrows, cs.fade, dilate=cs.shape == "fill")
        tint_cfg = cs
    for i in range(args.frames):
        t0 = time.perf_counter()
        r.step(1 / 6, p)
        tint = None
        if motion is not None:
            motion.feed(src.frame_at(i / 6), 1 / 6)
            tint = Tint.from_settings(heat.update(motion.mask, 1 / 6), tint_cfg, 1.0)
        t1 = time.perf_counter()
        img = r.draw(p, tint)
        t2 = time.perf_counter()
        buf = io.BytesIO()
        img.save(buf, format="PNG", compress_level=1)
        t3 = time.perf_counter()
        step_ms.append((t1 - t0) * 1000)
        draw_ms.append((t2 - t1) * 1000)
        enc_ms.append((t3 - t2) * 1000)
    total = [a + b + c for a, b, c in zip(step_ms, draw_ms, enc_ms)]

    def p95(xs):
        return sorted(xs)[int(len(xs) * 0.95) - 1]

    print(f"grid {args.cols}x{args.rows} -> frame {w}x{h}, state {args.state}, "
          f"motion tint {'on (synthetic source; step includes camera processing)' if args.motion else 'off'}, "
          f"{len(sprites.glyphs)} glyphs, font {sprites.font_path}")
    print(f"sprite cache build: {t_sprites:.0f} ms (once at start-up)")
    for name, xs in (("step", step_ms), ("draw", draw_ms), ("encode", enc_ms), ("total", total)):
        print(f"{name:7s} mean {statistics.mean(xs):6.1f} ms   p95 {p95(xs):6.1f} ms")
    print(f"PNG size about {len(buf.getvalue()) // 1024} KB")
    return 0


# ---------------------------------------------------------------- camera

RESTART_HINT = """Restart the daemon so it takes effect:
  pkill -f 'matrix[-_]rain daemon'
  osascript ~/Library/Application\\ Support/iTerm2/Scripts/AutoLaunch/iterm2-matrix-rain.scpt
(or run .venv/bin/matrix-rain daemon from an iTerm2 terminal)."""


def cmd_camera(args) -> int:
    from . import config
    from .camera import CameraSettings

    if args.action in ("enable", "disable"):
        path = config.set_key("camera", "enabled", args.action == "enable")
        print(f"camera {args.action}d in {path}")
        if args.action == "enable":
            try:
                import AVFoundation  # noqa: F401
            except Exception:
                print("The camera packages are not installed yet; run ./install.sh --camera")
        print(RESTART_HINT)
        return 0
    s = CameraSettings.from_cfg(config.load())
    if args.action == "status":
        try:
            import AVFoundation  # noqa: F401
            pkgs = "installed"
        except Exception:
            pkgs = "not installed (./install.sh --camera)"
        print(f"camera enabled: {'yes' if s.enabled else 'no'}  (config: {config.CONFIG_PATH})")
        print(f"camera packages: {pkgs}")
        print(f"tint {list(s.tint)}, head_tint {list(s.head_tint)}, fade {s.fade} s, sensitivity {s.sensitivity}, "
              f"strength {s.strength}, fill {s.fill}, fps {s.fps:g}, mirror {s.mirror}, only_when_active {s.only_when_active}, "
              f"device {s.device or '(default)'}")
        return 0
    # test
    from .camera import CameraSource, CameraUnavailable, MotionField, SyntheticSource

    src = SyntheticSource(fps=s.fps) if args.synthetic else CameraSource(s.device, s.fps)
    motion = MotionField(s)
    try:
        src.open()
    except CameraUnavailable as exc:
        print(f"camera not available: {exc}")
        return 1
    print(f"{src.name} open for {args.seconds:g} s; move in front of it.  Nothing is recorded.")
    print(f"sensitivity {s.sensitivity} -> threshold {motion.threshold} gray levels against the background; "
          f"warm-up {s.warmup:g} s")
    if getattr(src, "rate_note", ""):
        print(src.rate_note)
    try:
        start = time.monotonic()
        second, frames, cover, diffs, last_t, first = 1, 0, [], [], 0.0, True
        warming = False
        delivered0 = src.stats()["delivered"] if hasattr(src, "stats") else 0

        def report():
            nonlocal delivered0
            avg = sum(cover) / len(cover) if cover else 0.0
            peak = max(cover) if cover else 0.0
            line = f"  {second:2d}s  frames {frames:3d}  motion {avg * 100:5.1f}% avg  {peak * 100:5.1f}% peak"
            if warming:
                line += "  (warming up, no tint)"
            if args.verbose:
                mad = sum(diffs) / len(diffs) if diffs else 0.0
                line += f"  frame-to-frame diff {mad:5.2f} levels"
                if hasattr(src, "stats"):
                    st = src.stats()
                    line += f"  delivered {st['delivered'] - delivered0:3d}  dropped {st['dropped']}  errors {st['errors']}"
                    if st["last_error"]:
                        line += f" ({st['last_error'][:60]})"
                    delivered0 = st["delivered"]
            print(line)

        while time.monotonic() - start < args.seconds:
            img = src.read()   # None unless a new frame arrived
            if img is not None:
                t = src.last_time
                dt = t - last_t if last_t else 1.0 / s.fps
                last_t = t
                motion.feed(img, dt)
                warming = warming or motion.warming
                frames += 1
                cover.append(motion.coverage())
                if not first:
                    diffs.append(motion.last_frame_diff)
                first = False
            if time.monotonic() - start >= second:
                report()
                second, frames, cover, diffs, warming = second + 1, 0, [], [], False
            time.sleep(0.01)
        if frames:
            report()
    finally:
        src.close()
        print(f"{src.name} closed")
    return 0


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    from .config import STATES

    ap = argparse.ArgumentParser(prog="matrix-rain", add_help=True,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("daemon", help="animate opted-in iTerm2 sessions")
    d.add_argument("--session", action="append", default=[],
                   help="animate this session id only (repeatable); skips the profile-name opt-in")
    d.add_argument("--fps", type=float, help="override [daemon] fps")
    d.add_argument("--motion-demo", action="store_true",
                   help="feed a synthetic moving silhouette instead of the camera (never opens the camera)")
    d.add_argument("--update-now", action="store_true",
                   help="run the self-update step even if it ran within the last hour")
    d.add_argument("--verbose", "-v", action="store_true")

    e = sub.add_parser("event", help="send a state change for this terminal")
    e.add_argument("state", choices=STATES)
    e.add_argument("--tty", help="target the session on this tty, for example /dev/ttys003")
    e.add_argument("--session", help="target this iTerm2 session id")
    e.add_argument("--verbose", "-v", action="store_true", help="print what was sent to stderr")

    p = sub.add_parser("preview", help="render one frame to a PNG")
    p.add_argument("out")
    p.add_argument("--state", choices=STATES, default="working")
    p.add_argument("--cols", type=int, default=160)
    p.add_argument("--rows", type=int, default=45)
    p.add_argument("--warmup", type=float, default=8.0, help="seconds simulated before the frame")
    p.add_argument("--motion-demo", action="store_true", help="tint a synthetic moving silhouette gold")

    b = sub.add_parser("bench", help="measure frame time")
    b.add_argument("--cols", type=int, default=209)
    b.add_argument("--rows", type=int, default=51)
    b.add_argument("--state", choices=STATES, default="working")
    b.add_argument("--frames", type=int, default=120)
    b.add_argument("--motion", action="store_true", help="include the camera motion tint (synthetic source)")

    sub.add_parser("selftest", help="run built-in checks")
    c = sub.add_parser("camera", help="enable, disable, check or test the optional camera motion tint")
    c.add_argument("action", choices=["enable", "disable", "status", "test"])
    c.add_argument("--seconds", type=float, default=10.0, help="test duration")
    c.add_argument("--synthetic", action="store_true", help="test with the synthetic source, not the camera")
    c.add_argument("--verbose", "-v", action="store_true",
                   help="also print the mean frame-to-frame difference and frames delivered per second")
    u = sub.add_parser("update", help="fast-forward this clone from origin now")
    u.add_argument("--check-only", action="store_true", help="only report whether an update is available")
    sub.add_parser("version", help="print versions")
    sub.add_parser("status-component", help="print the camera toggle's Status Bar Layout entry for profiles/Matrix.json")
    return ap


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("help", "-h", "--help"):
        print(HELP)
        return 0
    is_event = argv[0] == "event"
    if not in_venv() and not os.environ.get("MATRIX_RAIN_ALLOW_SYSTEM_PYTHON"):
        print(VENV_HINT, file=sys.stderr)
        return 0 if is_event else 2
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return 0 if is_event else int(exc.code or 0)
    if args.cmd == "event":
        return cmd_event(args)
    if args.cmd == "preview":
        return cmd_preview(args)
    if args.cmd == "bench":
        return cmd_bench(args)
    if args.cmd == "selftest":
        from .selftest import main as selftest_main
        return selftest_main()
    if args.cmd == "camera":
        return cmd_camera(args)
    if args.cmd == "status-component":
        import json

        from .statusbar import layout_entry
        print(json.dumps(layout_entry(), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "version":
        from . import BUILT_FOR_ITERM2, __version__
        from .versions import compare, installed_iterm2

        installed = installed_iterm2()
        print(f"iterm2-matrix-rain {__version__}")
        print(f"built for iTerm2 {BUILT_FOR_ITERM2}")
        print(f"installed iTerm2 {installed or 'not found'} ({compare(installed)})")
        return 0
    if args.cmd == "update":
        from . import update
        from .versions import notify

        def say(msg):
            print(msg)

        updated = update.run(say, notify, force=True, check_only=args.check_only)
        return 0
    if args.cmd == "daemon":
        from .daemon import main as daemon_main
        return daemon_main(args)
    print(HELP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
