"""Text rain for any terminal: the same simulation drawn as colored glyphs.

The iTerm2 daemon paints the rain behind the text as an image.  Other
terminals (VTE/Ptyxis, GNOME Terminal, kitty, tmux, SSH) have no such API, so
this backend fills the terminal itself: alternate screen, one glyph per cell,
24-bit color when the terminal says it has it and the 256-color cube
otherwise.  Each frame redraws only the cells that changed, in one write
wrapped in synchronized output (DEC 2026; terminals without it ignore it).

Colors follow render._colorize: a tail cell is background + tail * level and
a head cell is background + head * brightness, drawn bold.  Glyph mirroring
and the glow halo are left out, since a terminal cell cannot draw them.

Events arrive on a datagram socket per process, CACHE_DIR/term/<pid>.sock,
which `matrix-rain event` (and so the Claude Code hook) fans out to.  Nothing
here imports Pillow.
"""

from __future__ import annotations

import json
import math
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .sim import DIGITS, KATAKANA, LATIN, Rain
from .state import Params, StateModel

# Alternate screen, cursor hidden, autowrap off (so writing the bottom-right
# cell never scrolls).  LEAVE undoes all of it and any open sync or SGR.
ENTER = "\x1b[?1049h\x1b[?25l\x1b[?7l"
LEAVE = "\x1b[?2026l\x1b[0m\x1b[?7h\x1b[?25h\x1b[?1049l"
SYNC_BEGIN, SYNC_END = "\x1b[?2026h", "\x1b[?2026l"

COLOR_MODES = ("auto", "truecolor", "256")
GLYPH_SETS = ("katakana", "ascii")
CUBE = (0, 95, 135, 175, 215, 255)

# Foreground channels are cut to 32 levels, so a fading tail changes a cell's
# color only every few frames and most lit cells need no redraw.
_Q32 = [round(round(v * 31 / 255) * 255 / 31) for v in range(256)]


def glyph_set(name: str) -> list[str]:
    """Glyphs for [terminal] glyphs: half-width katakana or printable ASCII only."""
    if name == "ascii":
        return DIGITS + LATIN
    return KATAKANA + DIGITS + LATIN[:8]


def _c6(v: int) -> int:
    return 0 if v < 48 else 1 if v < 115 else (v - 35) // 40


def rgb_to_256(r: int, g: int, b: int) -> int:
    """The nearest xterm 256-color index (6x6x6 cube or the gray ramp)."""
    cr, cg, cb = _c6(r), _c6(g), _c6(b)
    cube = 16 + 36 * cr + 6 * cg + cb
    dc = (r - CUBE[cr]) ** 2 + (g - CUBE[cg]) ** 2 + (b - CUBE[cb]) ** 2
    gi = max(0, min(23, ((r + g + b) // 3 - 3) // 10))
    gv = 8 + 10 * gi
    dg = (r - gv) ** 2 + (g - gv) ** 2 + (b - gv) ** 2
    return cube if dc <= dg else 232 + gi


def _tmux_has_rgb(env: dict) -> bool:
    try:
        out = subprocess.run(["tmux", "display-message", "-p", "#{client_termfeatures}"],
                             capture_output=True, text=True, timeout=0.5, env=env)
        return "RGB" in out.stdout
    except Exception:
        return False


def color_mode(setting: str = "auto", env: dict | None = None) -> str:
    """'truecolor' or '256' for [terminal] color.  There is no 16-color mode."""
    env = os.environ if env is None else env
    if setting in ("truecolor", "256"):
        return setting
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return "truecolor"
    if env.get("TMUX") and _tmux_has_rgb(dict(env)):
        return "truecolor"
    return "256"


def accepts(follow: str, mine: str | None, tty: str | None) -> bool:
    """Whether a rain following `follow` takes an event sent from `tty`.

    auto: inside tmux, only events from this tmux client's tty (`mine`);
    outside tmux there is no way to know which tab Claude runs in, so all.
    """
    if follow == "any":
        return True
    if follow.startswith("tty:"):
        return tty == follow[4:]
    return mine is None or tty == mine


# ---------------------------------------------------------------- sockets

def socket_dir() -> Path:
    """Where each term process binds its event socket (cli.send_event fans out here)."""
    return config.CACHE_DIR / "term"


def clean_stale(d: Path) -> int:
    """Remove sockets nothing is bound to (left by a killed term).  Returns the count."""
    removed = 0
    for path in d.glob("*.sock"):
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            probe.connect(str(path))
        except ConnectionRefusedError:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
        finally:
            probe.close()
    return removed


def open_socket(d: Path | None = None) -> tuple[socket.socket, Path]:
    d = d or socket_dir()
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    clean_stale(d)
    path = d / f"{os.getpid()}.sock"
    path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        sock.bind(str(path))
        os.chmod(path, 0o600)
        sock.setblocking(False)
    except OSError:
        sock.close()
        path.unlink(missing_ok=True)
        raise
    return sock, path


# ---------------------------------------------------------------- frames

class Screen:
    """Turns the simulation into escape sequences, redrawing only changed cells."""

    def __init__(self, cols: int, rows: int, ncols: int, mode: str, glyphs: list[str], rcfg: dict):
        self.mode = mode
        self.glyphs = glyphs
        self.gamma = float(rcfg.get("tail_gamma", 1.6))
        self.buckets = max(2, int(rcfg.get("buckets", 16)))
        self._sgr: dict = {}
        self._c256: dict = {}
        self.resize(cols, rows, ncols)

    def resize(self, cols: int, rows: int, ncols: int) -> None:
        self.cols, self.rows = max(1, int(cols)), max(1, int(rows))
        self.x0 = max(0, (self.cols - ncols) // 2)
        self.prev: dict | None = None   # None: repaint everything next frame
        self.bg = None

    def _fg(self, rgb: tuple, bold: bool) -> str:
        key = (rgb, bold)
        s = self._sgr.get(key)
        if s is None:
            if self.mode == "truecolor":
                s = f"\x1b[{1 if bold else 22};38;2;{rgb[0]};{rgb[1]};{rgb[2]}m"
            else:
                s = f"\x1b[{1 if bold else 22};38;5;{self._256(rgb)}m"
            self._sgr[key] = s
        return s

    def _256(self, rgb: tuple) -> int:
        n = self._c256.get(rgb)
        if n is None:
            n = self._c256[rgb] = rgb_to_256(*rgb)
        return n

    def cells(self, rain: Rain, p: Params) -> dict:
        """{cell index: (glyph index, (r, g, b), bold)} for every lit cell."""
        out = {}
        bright = max(0.0, min(1.0, p.brightness))
        steps = self.buckets - 1
        bg, tc, hc = p.background, p.tail, p.head
        q = _Q32
        head_rgb = (q[min(255, int(bg[0] + hc[0] * bright + 0.5))],
                    q[min(255, int(bg[1] + hc[1] * bright + 0.5))],
                    q[min(255, int(bg[2] + hc[2] * bright + 0.5))])
        head_lit = int(bright * steps + 0.5) > 0
        gamma, cols, x0 = self.gamma, self.cols, self.x0
        nrows = min(rain.nrows, self.rows)
        for c in range(min(rain.ncols, cols - x0)):
            if not rain.active[c]:
                continue
            hy = rain.head[c]
            L = rain.length[c]
            hrow = int(math.floor(hy))
            top = max(0, int(math.floor(hy - L)))
            bot = min(nrows - 1, hrow)
            if bot < top:
                continue
            col = rain.cells[c]
            for r in range(top, bot + 1):
                if r == hrow:
                    if head_lit:
                        out[r * cols + x0 + c] = (col[r], head_rgb, True)
                    continue
                d = (hy - r) / L
                if d >= 1.0:
                    continue
                level = (1.0 - d) ** gamma * bright * 0.85
                if int(level * steps + 0.5) <= 0:
                    continue
                out[r * cols + x0 + c] = (col[r], (q[min(255, int(bg[0] + tc[0] * level + 0.5))],
                                                   q[min(255, int(bg[1] + tc[1] * level + 0.5))],
                                                   q[min(255, int(bg[2] + tc[2] * level + 0.5))]), False)
        return out

    def frame(self, rain: Rain, p: Params) -> str:
        """The escape sequences that bring the terminal to this frame ('' if none)."""
        out: list[str] = []
        bg = tuple(max(0, min(255, int(v + 0.5))) for v in p.background)
        prev = self.prev
        if prev is None or bg != self.bg:
            # The background is the same in every cell, so a change repaints
            # the screen with it once; after that each cell sets only its fg.
            self.bg = bg
            if self.mode == "truecolor":
                out.append(f"\x1b[0;48;2;{bg[0]};{bg[1]};{bg[2]}m\x1b[2J")
            else:
                out.append(f"\x1b[0;48;5;{self._256(bg)}m\x1b[2J")
            prev = {}
            self.sgr = None
            self.cy = self.cx = -1
        cur = self.cells(rain, p)
        changed = [i for i, v in cur.items() if prev.get(i) != v]
        changed += [i for i in prev if i not in cur]
        changed.sort()
        cols, glyphs = self.cols, self.glyphs
        cy, cx, sgr = self.cy, self.cx, self.sgr
        for i in changed:
            y, x = divmod(i, cols)
            if y != cy or x < cx:
                out.append(f"\x1b[{y + 1};{x + 1}H")
            elif x > cx:
                out.append(f"\x1b[{x - cx}C")
            v = cur.get(i)
            if v is None:
                out.append(" ")
            else:
                key = (v[1], v[2])
                if key != sgr:
                    out.append(self._fg(*key))
                    sgr = key
                out.append(glyphs[v[0]])
            cy, cx = y, x + 1
        self.cy, self.cx, self.sgr = cy, cx, sgr
        self.prev = cur
        if not out:
            return ""
        return SYNC_BEGIN + "".join(out) + SYNC_END


# ---------------------------------------------------------------- the loop

def options(cfg: dict, args) -> dict:
    """Effective term settings from [terminal] and the command line.  Raises ValueError."""
    t = cfg.get("terminal", {})
    fps = float(getattr(args, "fps", None) or t.get("fps", 20))
    color = getattr(args, "color", None) or str(t.get("color", "auto"))
    glyphs = getattr(args, "glyphs", None) or str(t.get("glyphs", "katakana"))
    follow = getattr(args, "follow", None) or str(t.get("follow", "auto"))
    if not 0.5 <= fps <= 120:
        raise ValueError(f"fps {fps:g} is outside 0.5 to 120")
    if color not in COLOR_MODES:
        raise ValueError(f"color {color!r} is not one of {', '.join(COLOR_MODES)}")
    if glyphs not in GLYPH_SETS:
        raise ValueError(f"glyphs {glyphs!r} is not one of {', '.join(GLYPH_SETS)}")
    if follow not in ("auto", "any") and not (follow.startswith("tty:/dev/") and len(follow) > 9):
        raise ValueError(f"follow {follow!r} is not auto, any or tty:/dev/...")
    return {"fps": fps, "color": color, "glyphs": glyphs, "follow": follow,
            "max_cols": max(1, int(t.get("max_cols", 400))),
            "demo": bool(getattr(args, "demo", False)),
            "demo_interval": max(0.5, float(t.get("demo_interval", 5.0)))}


class TermRain:
    """Full-screen text rain on stdin/stdout until q, Ctrl-C, Ctrl-\\, SIGTERM or SIGHUP."""

    def __init__(self, cfg: dict, fps: float = 20.0, color: str = "auto", glyphs: str = "katakana",
                 follow: str = "auto", max_cols: int = 400, demo: bool = False,
                 demo_interval: float = 5.0, seed: int | None = None):
        self.cfg = cfg
        self.fps = fps
        self.mode = color_mode(color)
        self.glyphs = glyph_set(glyphs)
        self.follow = follow
        self.max_cols = max_cols
        self.demo = demo
        self.demo_interval = demo_interval
        self.seed = seed
        self.state = StateModel(cfg, transition=float(cfg["daemon"].get("transition", 1.0)))
        self.quit = self.resized = self.suspend = self.gone = False
        self.entered = False
        self._tmux_tty: str | None = None
        self._tmux_at = -1e9

    # ------------------------------------------------------------ terminal
    def _write(self, data: bytes) -> None:
        """Write everything, waiting out partial writes.  A closed terminal sets gone."""
        import select

        mv = memoryview(data)
        while mv and not self.gone:
            try:
                n = os.write(1, mv)
                mv = mv[n:]
            except BlockingIOError:
                select.select([], [1], [], 0.1)
            except InterruptedError:
                continue
            except OSError:
                self.gone = self.quit = True

    def _enter(self) -> None:
        import termios
        import tty

        self._attr = termios.tcgetattr(0)
        tty.setcbreak(0)                  # keys arrive at once, no echo; Ctrl-C still signals
        # No XON/XOFF: Ctrl-S would stall a frame write, and quit with it.  _leave restores it.
        attr = termios.tcgetattr(0)
        attr[0] &= ~termios.IXON
        termios.tcsetattr(0, termios.TCSANOW, attr)
        self._blocking = os.get_blocking(1)
        os.set_blocking(1, True)
        self.entered = True
        self._write(ENTER.encode())

    def _leave(self) -> None:
        if not self.entered:
            return
        import termios

        self.entered = False
        if not self.gone:
            self._write(LEAVE.encode())
        try:
            termios.tcsetattr(0, termios.TCSADRAIN, self._attr)
        except Exception:
            pass
        try:
            os.set_blocking(1, self._blocking)
        except OSError:
            pass

    def _size(self) -> tuple[int, int]:
        try:
            s = os.get_terminal_size(1)
            return max(1, s.columns), max(1, s.lines)
        except OSError:
            return 80, 24

    # ------------------------------------------------------------ events
    def _mine(self) -> str | None:
        """This process's tmux client tty, looked up at most every 5 s (clients change)."""
        if not os.environ.get("TMUX"):
            return None
        now = time.monotonic()
        if now - self._tmux_at > 5.0:
            from .cli import _tmux_client_tty

            self._tmux_tty = _tmux_client_tty(dict(os.environ))
            self._tmux_at = now
        return self._tmux_tty

    def apply(self, state, tty) -> bool:
        if state not in config.STATES:
            return False
        ok = accepts(self.follow, self._mine() if self.follow == "auto" else None, tty)
        if ok:
            self.state.set(state)
        return ok

    def _on_socket(self) -> None:
        while True:
            try:
                data = self.sock.recv(4096)
            except OSError:
                return
            try:
                msg = json.loads(data.decode())
            except Exception:
                continue
            if isinstance(msg, dict):
                self.apply(msg.get("state"), msg.get("tty"))

    def _on_keys(self) -> None:
        try:
            data = os.read(0, 64)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:                      # the terminal hung up
            self.gone = self.quit = True
            return
        data = data.split(b"\x1b", 1)[0]  # ignore arrow keys and other sequences
        for ch in data.decode("ascii", "ignore"):
            if ch in "qQ":
                self.quit = True
            elif "1" <= ch <= str(len(config.STATES)):
                self.state.set(config.STATES[int(ch) - 1])

    def _on_signal(self, signum, frame) -> None:
        if signum == signal.SIGWINCH:
            self.resized = True
        elif signum == signal.SIGTSTP:
            self.suspend = True
        else:
            self.quit = True   # SIGHUP too: restoring a closed terminal just fails quietly

    # ------------------------------------------------------------ main
    def run(self) -> int:
        import atexit
        import random
        import selectors

        if not (os.isatty(0) and os.isatty(1)):
            print("matrix-rain term draws full-screen and needs a terminal on stdin and stdout.",
                  file=sys.stderr)
            return 2
        cols, rows = self._size()
        rain = Rain(min(cols, self.max_cols), rows, len(self.glyphs), self.cfg["render"],
                    random.Random(self.seed))
        screen = Screen(cols, rows, rain.ncols, self.mode, self.glyphs, self.cfg["render"])
        try:
            self.sock, sock_path = open_socket()
        except OSError as exc:
            print(f"matrix-rain term: cannot open the event socket in {socket_dir()}: {exc}", file=sys.stderr)
            return 2
        # Everything after the socket is undone in the finally, however far setup got.
        wake_r = wake_w = sel = None
        saved: dict = {}
        old_wake = None
        try:
            wake_r, wake_w = os.pipe()
            os.set_blocking(wake_r, False)
            os.set_blocking(wake_w, False)
            handled = (signal.SIGWINCH, signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT,
                       signal.SIGTSTP)
            for s in handled:
                saved[s] = signal.signal(s, self._on_signal)
            old_wake = signal.set_wakeup_fd(wake_w, warn_on_full_buffer=False)
            sel = selectors.DefaultSelector()
            sel.register(0, selectors.EVENT_READ, self._on_keys)
            sel.register(self.sock.fileno(), selectors.EVENT_READ, self._on_socket)
            sel.register(wake_r, selectors.EVENT_READ, None)
            atexit.register(self._leave)
            self._enter()
            interval = 1.0 / self.fps
            last = next_t = time.monotonic()
            demo_at = last
            demo_i = 0
            if self.demo:
                self.state.set(config.STATES[0])
            while not self.quit:
                for key, _ in sel.select(max(0.0, next_t - time.monotonic())):
                    if key.data is None:
                        try:
                            while os.read(wake_r, 512):
                                pass
                        except OSError:
                            pass
                    else:
                        key.data()
                if self.quit:
                    break
                if self.suspend:
                    # Ctrl-Z: give the terminal back, stop, and repaint on return.
                    self.suspend = False
                    self._leave()
                    signal.signal(signal.SIGTSTP, signal.SIG_DFL)
                    os.kill(os.getpid(), signal.SIGTSTP)
                    signal.signal(signal.SIGTSTP, self._on_signal)
                    self._enter()
                    self.resized = True
                    last = next_t = time.monotonic()
                if self.resized:
                    self.resized = False
                    cols, rows = self._size()
                    rain.resize(min(cols, self.max_cols), rows)
                    screen.resize(cols, rows, rain.ncols)
                now = time.monotonic()
                if now < next_t:
                    continue
                if self.demo and now - demo_at >= self.demo_interval:
                    demo_at = now
                    demo_i = (demo_i + 1) % len(config.STATES)
                    self.state.set(config.STATES[demo_i])
                self.state.tick()
                p = self.state.current()
                rain.step(now - last, p)   # a late frame takes one longer step; frames never queue
                last = now
                data = screen.frame(rain, p)
                if data:
                    self._write(data.encode())
                next_t += interval
                if next_t < now:
                    next_t = now + interval
        finally:
            self._leave()
            atexit.unregister(self._leave)
            if old_wake is not None:
                signal.set_wakeup_fd(old_wake)
            for s, h in saved.items():
                signal.signal(s, h)
            if sel is not None:
                sel.close()
            for fd in (wake_r, wake_w):
                if fd is not None:
                    os.close(fd)
            self.sock.close()
            sock_path.unlink(missing_ok=True)
        return 0


def bench(cfg: dict, cols: int, rows: int, state: str, frames: int, color: str = "auto",
          glyphs: str = "katakana", fps: float = 20.0) -> dict:
    """Step and encode time and bytes per frame, with no terminal attached."""
    import random

    g = glyph_set(glyphs)
    mode = color_mode(color)
    rain = Rain(cols, rows, len(g), cfg["render"], random.Random(7))
    screen = Screen(cols, rows, rain.ncols, mode, g, cfg["render"])
    p = StateModel(cfg).palettes[state]
    for _ in range(int(8 * fps)):
        rain.step(1.0 / fps, p)
    first = len(screen.frame(rain, p).encode())
    step_ms, enc_ms, sizes = [], [], []
    for _ in range(frames):
        t0 = time.perf_counter()
        rain.step(1.0 / fps, p)
        t1 = time.perf_counter()
        data = screen.frame(rain, p).encode()
        t2 = time.perf_counter()
        step_ms.append((t1 - t0) * 1000)
        enc_ms.append((t2 - t1) * 1000)
        sizes.append(len(data))
    return {"mode": mode, "glyphs": len(g), "first": first, "step": step_ms, "encode": enc_ms, "bytes": sizes}
