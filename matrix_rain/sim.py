"""Rain simulation: column streams on a grid of glyph cells, with no drawing.

The iTerm2 renderer (render.Renderer) and the terminal backend (term.py) both
run this.  The order of the random number calls is part of the contract: the
renderer's frames for a fixed seed must stay byte-identical, so never add,
drop or reorder an rng call here.
"""

from __future__ import annotations

import math
import random

from .state import Params

# Half-width katakana (one terminal cell each), digits and Latin glyphs.
KATAKANA = [chr(c) for c in range(0xFF66, 0xFF9E)]
DIGITS = list("0123456789")
LATIN = list("ZTHEMAXRIX:.=*+-<>|")


class Rain:
    """Column state for an ncols x nrows grid of glyph indices below nglyphs."""

    def __init__(self, ncols: int, nrows: int, nglyphs: int, rcfg: dict,
                 rng: random.Random | None = None):
        self.rng = rng if rng is not None else random.Random()
        self.nglyphs = max(1, int(nglyphs))
        self.tail_min = int(rcfg.get("tail_min", 8))
        self.tail_max = int(rcfg.get("tail_max", 26))
        self.resize(ncols, nrows)

    def resize(self, ncols: int, nrows: int) -> None:
        """Start over on a new grid (the rng keeps going)."""
        self.ncols = max(1, int(ncols))
        self.nrows = max(1, int(nrows))
        n = self.nglyphs
        rng = self.rng
        self.cells = [[rng.randrange(n) for _ in range(self.nrows)] for _ in range(self.ncols)]
        self.active = [False] * self.ncols
        self.head = [0.0] * self.ncols
        self.length = [self.tail_min] * self.ncols
        self.factor = [1.0] * self.ncols
        self._seeded = False

    def _spawn(self, c: int, head: float | None = None) -> None:
        rng = self.rng
        self.active[c] = True
        self.length[c] = rng.randint(self.tail_min, self.tail_max)
        self.factor[c] = rng.uniform(0.6, 1.45)
        self.head[c] = head if head is not None else -rng.uniform(0.0, 4.0)

    def step(self, dt: float, p: Params) -> None:
        """Advance the simulation by dt seconds at the given parameters."""
        rng = self.rng
        dt = max(0.0, min(dt, 1.0))
        density = min(0.95, max(0.0, p.density))
        if not self._seeded:
            # Start mid-flow rather than with an empty screen.
            for c in range(self.ncols):
                if rng.random() < density:
                    self._spawn(c, head=rng.uniform(0, self.nrows + self.tail_max))
            self._seeded = True
        avg_len = (self.tail_min + self.tail_max) / 2
        run_time = (self.nrows + avg_len) / max(0.1, p.speed)
        if density <= 0.001:
            rate = 0.0
        else:
            mean_wait = max(0.05, run_time * (1 - density) / density)
            rate = 1.0 / mean_wait
        spawn_p = 1.0 - math.exp(-rate * dt)
        n = self.nglyphs
        mut = max(0.0, p.mutation) * dt
        for c in range(self.ncols):
            if self.active[c]:
                self.head[c] += p.speed * self.factor[c] * dt
                L = self.length[c]
                if self.head[c] - L > self.nrows:
                    self.active[c] = False
                    continue
                # Glyph mutation within the visible stretch.
                expected = mut * L
                k = int(expected) + (1 if rng.random() < expected - int(expected) else 0)
                if k:
                    col = self.cells[c]
                    top = max(0, int(self.head[c] - L))
                    bot = min(self.nrows - 1, int(self.head[c]))
                    if bot >= top:
                        for _ in range(k):
                            col[rng.randint(top, bot)] = rng.randrange(n)
            elif rng.random() < spawn_p:
                self._spawn(c)
