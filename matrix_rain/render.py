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

from .sim import DIGITS, KATAKANA, LATIN, Rain
from .state import Params


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
    """Draws the rain simulation (sim.Rain) for one frame size."""

    def __init__(self, width: int, height: int, rcfg: dict, seed: int | None = None,
                 sprites: SpriteCache | None = None):
        self.rcfg = rcfg
        self.rng = random.Random(seed)
        self.sprites = sprites or SpriteCache(rcfg)
        self.gamma = float(rcfg.get("tail_gamma", 1.6))
        self.rain: Rain | None = None
        self.resize(width, height)

    def resize(self, width: int, height: int) -> None:
        self.width, self.height = int(width), int(height)
        cw, ch = self.sprites.cw, self.sprites.ch
        ncols = max(1, self.width // cw)
        nrows = max(1, -(-self.height // ch))
        self.x0 = (self.width - ncols * cw) // 2
        if self.rain is None:
            self.rain = Rain(ncols, nrows, len(self.sprites.glyphs), self.rcfg, self.rng)
        else:
            self.rain.resize(ncols, nrows)

    # The simulation's state, read by draw() and by the daemon and selftest.
    ncols = property(lambda self: self.rain.ncols)
    nrows = property(lambda self: self.rain.nrows)
    cells = property(lambda self: self.rain.cells)
    active = property(lambda self: self.rain.active)
    head = property(lambda self: self.rain.head)
    length = property(lambda self: self.rain.length)

    def step(self, dt: float, p: Params) -> None:
        """Advance the simulation by dt seconds at the given parameters."""
        self.rain.step(dt, p)

    def draw(self, p: Params, tint=None) -> Image.Image:
        """Return an RGB frame for the current simulation state.

        ``tint`` (a camera.Tint) lerps each cell's tail and head colors toward
        the motion colors by that cell's heat times strength.  With no tint, or
        no heat anywhere, the output is exactly the untinted frame.
        """
        w, h = self.width, self.height
        tails = Image.new("L", (w, h), 0)
        heads = Image.new("L", (w, h), 0)
        cw, ch = self.sprites.cw, self.sprites.ch
        m = self.sprites.margin
        bright = max(0.0, min(1.0, p.brightness))
        gamma = self.gamma
        get = self.sprites.get
        masks = self.sprites.masks
        if tint is not None and tint.fill > 0:
            self._fill_moving(tails, tint, bright)
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
        base = _colorize(tails, heads, p.background, p.tail, p.head)
        if tint is None or tint.strength <= 0 or tint.heat.getextrema()[1] == 0:
            return base
        gold = _colorize(tails, heads, p.background, tint.tail, tint.head, getattr(tint, "gain", 1.0))
        mask = self._cell_mask(tint.heat, tint.strength)
        return Image.composite(gold, base, mask)

    def _fill_moving(self, tails: Image.Image, tint, bright: float) -> None:
        """Light every moving cell's glyph dimly so the moving shape reads
        even where no stream is falling.  Streams draw over it."""
        heat = tint.heat
        if heat.size != (self.ncols, self.nrows):
            heat = heat.resize((self.ncols, self.nrows), Image.Resampling.BOX)
        if heat.getextrema()[1] == 0:
            return
        cw, ch, m = self.sprites.cw, self.sprites.ch, self.sprites.margin
        get, masks = self.sprites.get, self.sprites.masks
        data = heat.tobytes()
        scale = tint.fill * bright / 255.0
        n = self.ncols
        for i, v in enumerate(data):
            if v < 16:
                continue
            c, r = i % n, i // n
            g = self.cells[c][r]
            spr = get(g, v * scale)
            if spr is not None:
                tails.paste(spr, (self.x0 + c * cw - m, r * ch - m), masks[g])

    def _cell_mask(self, heat: Image.Image, strength: float) -> Image.Image:
        """Frame-size 'L' mask: each rain cell carries its heat times strength."""
        cw, ch = self.sprites.cw, self.sprites.ch
        if heat.size != (self.ncols, self.nrows):
            heat = heat.resize((self.ncols, self.nrows), Image.Resampling.BOX)
        if strength < 1.0:
            heat = heat.point([int(v * strength + 0.5) for v in range(256)])
        big = heat.resize((self.ncols * cw, self.nrows * ch), Image.Resampling.BILINEAR)
        mask = Image.new("L", (self.width, self.height), 0)
        mask.paste(big, (self.x0, 0))
        return mask


def _colorize(tails: Image.Image, heads: Image.Image, bg, tc, hc, gain: float = 1.0) -> Image.Image:
    """Map the gray canvases to color; gain brightens the glyphs (burst)."""
    if gain == 1.0:  # the original formula, kept exact so untinted frames never change
        tail_luts = [[min(255, int(bg[i] + tc[i] * v / 255.0 + 0.5)) for v in range(256)] for i in range(3)]
        head_luts = [[min(255, int(hc[i] * v / 255.0 + 0.5)) for v in range(256)] for i in range(3)]
    else:
        k = gain / 255.0
        tail_luts = [[min(255, int(bg[i] + tc[i] * min(1.0, v * k) + 0.5)) for v in range(256)] for i in range(3)]
        head_luts = [[min(255, int(hc[i] * min(1.0, v * k) + 0.5)) for v in range(256)] for i in range(3)]
    tail_rgb = Image.merge("RGB", [tails.point(lut) for lut in tail_luts])
    head_rgb = Image.merge("RGB", [heads.point(lut) for lut in head_luts])
    return ImageChops.add(tail_rgb, head_rgb)


def save_png(img: Image.Image, path: Path) -> None:
    """Write atomically so iTerm2 never reads a half-written file."""
    tmp = path.with_name(path.name + ".tmp")
    img.save(tmp, format="PNG", compress_level=1)
    os.replace(tmp, path)
