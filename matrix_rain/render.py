"""Frame renderer: soft digital rain drawn with cached Pillow glyph sprites.

Fonts are rasterized once at start-up into grayscale sprites, one per glyph
per brightness level.  Each frame pastes sprites into two grayscale canvases
(tails and heads) and colorizes them with lookup tables, so a palette change
costs nothing and no font is rendered per frame.
"""

from __future__ import annotations

import math
import os
import random
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .state import Params

KATAKANA = [chr(c) for c in range(0xFF66, 0xFF9E)]
DIGITS = list("0123456789")
LATIN = list("ZTHEMAXRIX:.=*+-<>|")


def frame_size(cols: int, rows: int, dcfg: dict) -> tuple[int, int]:
    """Frame pixel size for a terminal grid: about half the window, capped."""
    cols, rows = max(1, int(cols)), max(1, int(rows))
    full_w = cols * int(dcfg.get("px_per_col", 8))
    full_h = rows * int(dcfg.get("px_per_row", 16))
    w = min(int(dcfg.get("max_width", 1200)), full_w)
    h = max(16, round(full_h * w / full_w))
    return max(16, w), h


def _raster(font, ch: str) -> bytes:
    img = Image.new("L", (64, 64), 0)
    ImageDraw.Draw(img).text((8, 8), ch, fill=255, font=font)
    return img.tobytes()


def _font_has(font, ch: str) -> bool:
    """True when the font draws ch as something other than its missing-glyph box."""
    try:
        drawn = _raster(font, ch)
        return any(drawn) and drawn != _raster(font, "\uffff") and drawn != _raster(font, "\U0010fffd")
    except Exception:
        return False


def load_glyph_font(rcfg: dict):
    """Return (font, glyph list).  Falls back to digits and Latin letters."""
    size = int(rcfg.get("font_size", 14))
    for path in rcfg.get("fonts", []):
        if not os.path.exists(path):
            continue
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            continue
        if _font_has(font, "ｱ") and _font_has(font, "ﾊ"):
            kana = [c for c in KATAKANA if _font_has(font, c)]
            return font, kana + DIGITS + LATIN[:8], path
    for path in rcfg.get("latin_fonts", []):
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), DIGITS + LATIN, path
            except Exception:
                continue
    return ImageFont.load_default(size=size), DIGITS + LATIN, "pillow-default"


class SpriteCache:
    """One grayscale sprite per glyph per brightness bucket, built once."""

    def __init__(self, rcfg: dict):
        self.cw = int(rcfg.get("cell_width", 12))
        self.ch = int(rcfg.get("cell_height", 16))
        self.buckets = max(2, int(rcfg.get("buckets", 16)))
        glow = float(rcfg.get("glow", 0.5))
        radius = float(rcfg.get("glow_radius", 2.0))
        self.margin = int(math.ceil(radius * 2)) if glow > 0 else 0
        font, glyphs, self.font_path = load_glyph_font(rcfg)
        self.glyphs = glyphs
        size = (self.cw + 2 * self.margin, self.ch + 2 * self.margin)
        self.sprites: list[list[Image.Image | None]] = []
        self.masks: list[Image.Image] = []
        for g in glyphs:
            base = Image.new("L", size, 0)
            draw = ImageDraw.Draw(base)
            l, t, r, b = draw.textbbox((0, 0), g, font=font)
            x = self.margin + (self.cw - (r - l)) / 2 - l
            y = self.margin + (self.ch - (b - t)) / 2 - t
            draw.text((x, y), g, fill=255, font=font)
            if rcfg.get("mirror", True):
                base = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            # Slight softening of the glyph itself, then a wide faint halo.
            core = base.filter(ImageFilter.GaussianBlur(0.35))
            if glow > 0:
                halo = base.filter(ImageFilter.GaussianBlur(radius)).point(
                    lambda v, k=glow * 1.6: min(255, int(v * k))
                )
                core = ImageChops.lighter(core, halo)
            # Alpha mask normalized to full strength so pasting a dim level
            # does not square its brightness.
            peak = core.getextrema()[1] or 1
            self.masks.append(core.point(lambda v, k=255.0 / peak: min(255, int(v * k + 0.5))))
            levels: list[Image.Image | None] = [None]
            for i in range(1, self.buckets):
                f = i / (self.buckets - 1)
                levels.append(core.point(lambda v, f=f: int(v * f + 0.5)))
            self.sprites.append(levels)

    def get(self, glyph: int, level: float) -> Image.Image | None:
        idx = int(level * (self.buckets - 1) + 0.5)
        if idx <= 0:
            return None
        return self.sprites[glyph][min(idx, self.buckets - 1)]


class Renderer:
    """Holds the rain simulation for one frame size."""

    def __init__(self, width: int, height: int, rcfg: dict, seed: int | None = None,
                 sprites: SpriteCache | None = None):
        self.rcfg = rcfg
        self.rng = random.Random(seed)
        self.sprites = sprites or SpriteCache(rcfg)
        self.tail_min = int(rcfg.get("tail_min", 8))
        self.tail_max = int(rcfg.get("tail_max", 26))
        self.gamma = float(rcfg.get("tail_gamma", 1.6))
        self.resize(width, height)

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = int(width), int(height)
        cw, ch = self.sprites.cw, self.sprites.ch
        self.ncols = max(1, self.width // cw)
        self.nrows = max(1, -(-self.height // ch))
        self.x0 = (self.width - self.ncols * cw) // 2
        n = len(self.sprites.glyphs)
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
        n = len(self.sprites.glyphs)
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

    def draw(self, p: Params) -> Image.Image:
        """Return an RGB frame for the current simulation state."""
        w, h = self.width, self.height
        tails = Image.new("L", (w, h), 0)
        heads = Image.new("L", (w, h), 0)
        cw, ch = self.sprites.cw, self.sprites.ch
        m = self.sprites.margin
        bright = max(0.0, min(1.0, p.brightness))
        gamma = self.gamma
        get = self.sprites.get
        masks = self.sprites.masks
        for c in range(self.ncols):
            if not self.active[c]:
                continue
            hy = self.head[c]
            L = self.length[c]
            hrow = int(math.floor(hy))
            top = max(0, int(math.floor(hy - L)))
            bot = min(self.nrows - 1, hrow)
            if bot < top:
                continue
            x = self.x0 + c * cw - m
            col = self.cells[c]
            for r in range(top, bot + 1):
                y = r * ch - m
                if r == hrow:
                    spr = get(col[r], bright)
                    if spr is not None:
                        heads.paste(spr, (x, y), masks[col[r]])
                    continue
                d = (hy - r) / L
                if d >= 1.0:
                    continue
                level = (1.0 - d) ** gamma * bright * 0.85
                spr = get(col[r], level)
                if spr is not None:
                    tails.paste(spr, (x, y), masks[col[r]])
        bg, tc, hc = p.background, p.tail, p.head
        tail_luts = [[min(255, int(bg[i] + tc[i] * v / 255.0 + 0.5)) for v in range(256)] for i in range(3)]
        head_luts = [[min(255, int(hc[i] * v / 255.0 + 0.5)) for v in range(256)] for i in range(3)]
        tail_rgb = Image.merge("RGB", [tails.point(lut) for lut in tail_luts])
        head_rgb = Image.merge("RGB", [heads.point(lut) for lut in head_luts])
        return ImageChops.add(tail_rgb, head_rgb)


def save_png(img: Image.Image, path: Path) -> None:
    """Write atomically so iTerm2 never reads a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    img.save(tmp, format="PNG", compress_level=1)
    os.replace(tmp, path)
