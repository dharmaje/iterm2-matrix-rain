"""The daemon: renders rain per opted-in iTerm2 session and swaps frames.

Frames are PNG files in a ring of three names per session under
~/.cache/iterm2-matrix-rain/<session_id>/, because iTerm2 may cache an image
by path and does not animate GIF backgrounds.  Only sessions in the selected
tab of the key window animate at full rate while iTerm2 is the active app;
everything else gets one frame every few seconds.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import sys
import time
from pathlib import Path

from . import config
from .render import Renderer, SpriteCache, frame_size, save_png
from .state import StateModel

RING = 3


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S ") + msg, file=sys.stderr, flush=True)


class Animated:
    """One animated session."""

    def __init__(self, session, tty, cols, rows, cfg, sprites):
        self.sid = session.session_id
        self.tty = tty
        self.cols, self.rows = cols, rows
        self.cfg = cfg
        w, h = frame_size(cols, rows, cfg["daemon"])
        self.renderer = Renderer(w, h, cfg["render"], sprites=sprites)
        self.state = StateModel(cfg, transition=cfg["daemon"].get("transition", 1.0))
        self.dir = config.CACHE_DIR / self.sid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ring = 0
        self.last_frame = 0.0
        self.last_step = time.monotonic()
        self.initialized = False
        self.orig_blend: float | None = None
        self.frames = 0

    def resize(self, cols, rows) -> None:
        if (cols, rows) == (self.cols, self.rows):
            return
        self.cols, self.rows = cols, rows
        w, h = frame_size(cols, rows, self.cfg["daemon"])
        self.renderer.resize(w, h)

    def render_next(self) -> Path:
        """Blocking: advance, draw, write.  Runs in a worker thread."""
        now = time.monotonic()
        dt = now - self.last_step
        self.last_step = now
        self.state.tick()
        p = self.state.current()
        self.renderer.step(dt, p)
        img = self.renderer.draw(p)
        path = self.dir / f"frame{self.ring}.png"
        self.ring = (self.ring + 1) % RING
        save_png(img, path)
        return path


class Daemon:
    def __init__(self, connection, cfg, overrides, verbose=False):
        self.conn = connection
        self.cfg = cfg
        self.d = cfg["daemon"]
        self.overrides = set(overrides)
        self.verbose = verbose
        self.sessions: dict[str, Animated] = {}
        self.app = None
        self.sprites = SpriteCache(cfg["render"])
        self.stop = asyncio.Event()
        self.wake = asyncio.Event()
        self.sock: socket.socket | None = None
        self.warned_missing: set[str] = set()

    # ------------------------------------------------------------ helpers
    def focused(self, sid: str) -> bool:
        app = self.app
        if not app or not app.app_active:
            return False
        s = app.get_session_by_id(sid)
        if s is None:
            return False
        window, tab = app.get_tab_and_window_for_session(s)
        if window is None or tab is None:
            return False
        if window.window_id != app.current_terminal_window_id:
            return False
        return window.selected_tab_id == tab.tab_id

    async def set_frame(self, a: Animated, path: Path) -> bool:
        import iterm2

        s = self.app.get_session_by_id(a.sid)
        if s is None:
            return False
        prof = iterm2.LocalWriteOnlyProfile()
        prof.set_background_image_location(str(path))
        if not a.initialized:
            prof.set_background_image_mode(iterm2.BackgroundImageMode.STRETCH)
            prof.set_blend(float(self.d.get("blend", 0.6)))
        await s.async_set_profile_properties(prof)
        a.initialized = True
        return True

    async def restore(self, a: Animated) -> None:
        import iterm2

        s = self.app.get_session_by_id(a.sid) if self.app else None
        if s is None:
            return
        prof = iterm2.LocalWriteOnlyProfile()
        prof.set_background_image_location("")
        prof.set_blend(a.orig_blend if a.orig_blend is not None else 0.5)
        try:
            await s.async_set_profile_properties(prof)
            log(f"restored {a.sid}")
        except Exception as exc:
            log(f"restore failed for {a.sid}: {exc}")

    # ------------------------------------------------------------ discovery
    async def discover(self) -> None:
        import iterm2

        app = self.app
        seen = set()
        for window in app.terminal_windows:
            for tab in window.tabs:
                for s in tab.sessions:
                    sid = s.session_id
                    try:
                        if self.overrides:
                            want = sid in self.overrides
                        else:
                            want = (await s.async_get_variable("profileName")) == self.d.get("profile", "Matrix")
                        if not want:
                            if sid in self.sessions:
                                await self.restore(self.sessions.pop(sid))
                            continue
                        seen.add(sid)
                        cols = int(await s.async_get_variable("columns") or 80)
                        rows = int(await s.async_get_variable("rows") or 24)
                        if sid in self.sessions:
                            self.sessions[sid].resize(cols, rows)
                            continue
                        tty = await s.async_get_variable("tty")
                        a = Animated(s, tty, cols, rows, self.cfg, self.sprites)
                        try:
                            a.orig_blend = (await s.async_get_profile()).blend
                        except Exception:
                            a.orig_blend = None
                        self.sessions[sid] = a
                        log(f"animating {sid} tty={tty} grid={cols}x{rows} "
                            f"frame={a.renderer.width}x{a.renderer.height}")
                    except iterm2.RPCException as exc:
                        if self.verbose:
                            log(f"skip {sid}: {exc}")
        for sid in list(self.sessions):
            if sid not in seen:
                self.sessions.pop(sid)
                shutil.rmtree(config.CACHE_DIR / sid, ignore_errors=True)
                log(f"session gone: {sid}")
        for sid in self.overrides - seen:
            if sid not in self.warned_missing:
                self.warned_missing.add(sid)
                log(f"--session {sid} not found")

    async def discover_loop(self) -> None:
        interval = float(self.d.get("discover_interval", 5.0))
        while not self.stop.is_set():
            try:
                await self.app.async_refresh()
                await self.discover()
            except Exception as exc:
                log(f"discovery error: {exc}")
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------ events
    def open_socket(self) -> None:
        path = config.SOCKET_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            # Refuse to steal a live daemon's socket.
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            try:
                probe.connect(str(path))
                probe.close()
                raise SystemExit(f"another daemon is listening on {path}")
            except (ConnectionRefusedError, FileNotFoundError):
                path.unlink(missing_ok=True)
            finally:
                probe.close()
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(str(path))
        os.chmod(path, 0o600)
        self.sock.setblocking(False)
        asyncio.get_running_loop().add_reader(self.sock.fileno(), self.on_datagram)

    def on_datagram(self) -> None:
        while True:
            try:
                data = self.sock.recv(4096)
            except (BlockingIOError, OSError):
                return
            try:
                msg = json.loads(data.decode())
                self.apply(msg.get("state"), msg.get("tty"), msg.get("session"))
            except Exception as exc:
                log(f"bad event: {exc}")

    def apply(self, state, tty, session) -> None:
        if state not in config.STATES:
            return
        target = None
        if session:
            session = session.split(":", 1)[-1]
            target = self.sessions.get(session)
        if target is None and tty:
            target = next((a for a in self.sessions.values() if a.tty == tty), None)
        if target is None:
            if self.verbose:
                log(f"event {state} for tty={tty} session={session}: no animated session")
            return
        target.state.set(state)
        log(f"{target.sid[:8]} {state}")
        self.wake.set()

    # ------------------------------------------------------------ focus
    async def focus_loop(self) -> None:
        import iterm2

        async with iterm2.FocusMonitor(self.conn) as mon:
            while not self.stop.is_set():
                update = await mon.async_get_next_update()
                if self.verbose and (update.application_active or update.window_changed):
                    log(f"focus: {update}")
                self.wake.set()

    # ------------------------------------------------------------ frames
    async def render_loop(self) -> None:
        loop = asyncio.get_running_loop()
        fps = max(0.2, float(self.d.get("fps", 6.0)))
        fast = 1.0 / fps
        slow = max(fast, float(self.d.get("idle_interval", 2.0)))
        while not self.stop.is_set():
            now = time.monotonic()
            next_due = now + slow
            for a in list(self.sessions.values()):
                quiet = a.state.target == "off" and a.state.settled()
                interval = fast if (self.focused(a.sid) and not quiet) else slow
                due = a.last_frame + interval
                if now >= due - 0.005:
                    a.last_frame = now
                    try:
                        path = await loop.run_in_executor(None, a.render_next)
                        await self.set_frame(a, path)
                        a.frames += 1
                    except Exception as exc:
                        log(f"frame error for {a.sid}: {exc}")
                    due = now + interval
                next_due = min(next_due, due)
            self.wake.clear()
            timeout = max(0.0, next_due - time.monotonic())
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------ run
    async def watch_connection(self) -> None:
        """Stop when iTerm2 closes the API connection (for example on quit)."""
        ws = getattr(self.conn, "websocket", None)
        if ws is None or not hasattr(ws, "wait_closed"):
            return
        await ws.wait_closed()
        if not self.stop.is_set():
            self.connection_closed = True
            log("iTerm2 closed the connection, exiting")
            self.stop.set()

    async def run(self) -> None:
        import iterm2

        self.connection_closed = False
        self.app = await iterm2.async_get_app(self.conn)
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            loop.add_signal_handler(sig, self.stop.set)
        self.open_socket()
        log(f"matrix-rain daemon pid {os.getpid()}, socket {config.SOCKET_PATH}, "
            f"fps {self.d.get('fps')}, font {self.sprites.font_path}, "
            + (f"sessions {sorted(self.overrides)}" if self.overrides
               else f"profile \"{self.d.get('profile')}\""))
        tasks = [asyncio.create_task(self.watch_connection())]
        try:
            await self.app.async_refresh()
            await self.discover()
            tasks += [asyncio.create_task(t) for t in
                      (self.discover_loop(), self.render_loop(), self.focus_loop())]
            await self.stop.wait()
        finally:
            log("stopping")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for a in list(self.sessions.values()):
                if not self.connection_closed:
                    try:
                        await asyncio.wait_for(self.restore(a), timeout=3)
                    except Exception as exc:
                        log(f"restore timed out for {a.sid}: {exc}")
                shutil.rmtree(a.dir, ignore_errors=True)
            try:
                loop.remove_reader(self.sock.fileno())
                self.sock.close()
            except Exception:
                pass
            config.SOCKET_PATH.unlink(missing_ok=True)
            log("stopped")


CONNECT_TIMEOUT = 15.0


def _quiet_closed_connection() -> None:
    """Keep a closed iTerm2 connection out of the log as a traceback.

    The iterm2 module's message reader prints a traceback for any error,
    including the expected one when iTerm2 quits.  The daemon already logs one
    line for that case, so the module's print is skipped for closed-connection
    errors and kept for everything else.
    """
    import traceback as _tb
    import types

    import iterm2.connection as conn_mod
    import websockets.exceptions as wsx

    def print_exc(*args, **kwargs):
        exc = sys.exc_info()[1]
        if isinstance(exc, wsx.ConnectionClosed):
            return
        _tb.print_exc(*args, **kwargs)

    shim = types.SimpleNamespace(**{k: getattr(_tb, k) for k in dir(_tb) if not k.startswith("__")})
    shim.print_exc = print_exc
    conn_mod.traceback = shim


def main(args) -> int:
    import threading

    try:
        import iterm2
    except ImportError:
        print("The iterm2 package is missing; run ./install.sh --install", file=sys.stderr)
        return 2
    cfg = config.load()
    if args.fps:
        cfg["daemon"]["fps"] = args.fps

    from . import update, versions

    if not versions.check_iterm2(log):
        return 1
    if not os.environ.get("MATRIX_RAIN_REEXEC"):
        try:
            if update.run(log, versions.notify, force=getattr(args, "update_now", False)):
                log("restarting with the updated code")
                argv = [sys.executable, "-m", "matrix_rain"] + sys.argv[1:]
                os.environ["MATRIX_RAIN_REEXEC"] = "1"
                os.execv(sys.executable, argv)
        except Exception as exc:  # the update step never blocks the daemon
            log(f"update: failed ({exc}); continuing with the current code")

    # The iterm2 module asks iTerm2 for an API cookie with a blocking
    # osascript call when ITERM2_COOKIE is not set, and that call never
    # returns for a process without Automation permission (launchd jobs).
    # A watchdog thread turns that hang into a clear exit.
    def give_up() -> None:
        log(f"could not connect to iTerm2 within {CONNECT_TIMEOUT:.0f} s "
            "(is iTerm2 running with the Python API enabled?  Start the daemon "
            "from iTerm2's AutoLaunch script or an iTerm2 terminal); exiting")
        os._exit(1)

    _quiet_closed_connection()

    watchdog = threading.Timer(CONNECT_TIMEOUT, give_up)
    watchdog.daemon = True

    async def entry(connection):
        watchdog.cancel()
        await Daemon(connection, cfg, args.session, verbose=args.verbose).run()

    watchdog.start()
    try:
        # Connection().run_until_complete, not iterm2.run_until_complete: the
        # module-level helper turns a refused connection into a silent exit(1).
        iterm2.Connection().run_until_complete(entry, False)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        watchdog.cancel()
        log(f"could not connect to iTerm2: {exc.__class__.__name__}: {exc}; exiting")
        return 1
    finally:
        watchdog.cancel()
    return 0
