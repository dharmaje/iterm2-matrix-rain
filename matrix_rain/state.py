"""State model: eases between palettes and tempos so changes never pop."""

from __future__ import annotations

import time
from dataclasses import dataclass, fields

PERSISTENT = ("idle", "working", "waiting", "off")
TRANSIENT = ("error", "done")


@dataclass
class Params:
    background: tuple
    tail: tuple
    head: tuple
    speed: float
    density: float
    brightness: float
    mutation: float

    @classmethod
    def from_cfg(cls, d: dict) -> "Params":
        return cls(
            background=tuple(float(c) for c in d["background"]),
            tail=tuple(float(c) for c in d["tail"]),
            head=tuple(float(c) for c in d["head"]),
            speed=float(d["speed"]),
            density=float(d["density"]),
            brightness=float(d["brightness"]),
            mutation=float(d["mutation"]),
        )

    def lerp(self, other: "Params", t: float) -> "Params":
        out = {}
        for f in fields(self):
            a, b = getattr(self, f.name), getattr(other, f.name)
            if isinstance(a, tuple):
                out[f.name] = tuple(x + (y - x) * t for x, y in zip(a, b))
            else:
                out[f.name] = a + (b - a) * t
        return Params(**out)


def ease(t: float) -> float:
    """Smootherstep: zero slope at both ends."""
    t = min(1.0, max(0.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


class StateModel:
    """Tracks the target state and interpolates the rendered parameters.

    Persistent states (idle, working, waiting, off) stay until replaced.
    Transient states (error, done) hold for their configured time, then ease
    to their ``next`` state; persistent events arriving during the hold update
    that destination instead of cutting the transient short.  ``off`` always
    applies at once.
    """

    def __init__(self, cfg: dict, transition: float = 1.0, clock=time.monotonic):
        self.cfg = cfg["states"]
        self.palettes = {k: Params.from_cfg(v) for k, v in self.cfg.items()}
        self.transition = max(0.01, float(transition))
        self.clock = clock
        self.base = "idle"
        self.target = "idle"
        self.transient_until: float | None = None
        self._from = self.palettes["idle"]
        self._start = self.clock() - self.transition
        self._current = self._from

    def _retarget(self, name: str) -> None:
        self._from = self.current()
        self._start = self.clock()
        self.target = name

    def set(self, name: str) -> None:
        if name not in self.palettes:
            return
        now = self.clock()
        if name in TRANSIENT:
            nxt = self.cfg[name].get("next", "previous")
            if nxt != "previous":
                self.base = nxt
            self.transient_until = now + float(self.cfg[name].get("hold", 4.0))
            self._retarget(name)
            return
        self.base = name
        if self.transient_until is not None and now < self.transient_until and name != "off":
            return
        self.transient_until = None
        if name != self.target:
            self._retarget(name)

    def tick(self) -> None:
        if self.transient_until is not None and self.clock() >= self.transient_until:
            self.transient_until = None
            if self.base != self.target:
                self._retarget(self.base)

    def settled(self) -> bool:
        return self.transient_until is None and self.clock() - self._start >= self.transition

    def current(self) -> Params:
        t = ease((self.clock() - self._start) / self.transition)
        return self._from.lerp(self.palettes[self.target], t)
