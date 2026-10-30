"""Optional camera motion: where something moved, the rain turns gold.

Pipeline (Pillow only, about 320x240 grayscale at ~10 fps):
  frame -> exponential running background -> absolute difference ->
  threshold (from `sensitivity`) -> small blur = motion mask (camera size)
Each rain grid then keeps a per-cell heat image: the mask is downsampled to
one value per rain cell, dilated by one cell, and heat = max(heat - dt/fade,
motion), so it rises at once and fades to zero over `fade` seconds.

Nothing is recorded or written to disk; frames live only in memory.

The camera uses PyObjC AVFoundation from requirements-camera.txt, imported
lazily.  SyntheticSource draws a drifting torso and head and stands in for the
camera in tests, previews and `daemon --motion-demo`.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter

CAM_W, CAM_H = 320, 240


# ------------------------------------------------------------------ settings

@dataclass
class CameraSettings:
    enabled: bool = False
    tint: tuple = (230, 180, 60)
    head_tint: tuple = (255, 232, 150)
    fade: float = 1.5
    fade_curve: float = 2.5
    idle_after: float = 1.0
    idle_fade: float = 0.3
    gain: float = 1.5
    sensitivity: float = 0.5
    strength: float = 1.0
    fill: float = 0.4
    warmup: float = 1.5
    shape: str = "outline"
    outline_width: int = 3
    burst: float = 3.0
    burst_rest: float = 2.0
    burst_strength: float = 1.4
    burst_tint: tuple = (245, 200, 80)
    burst_head_tint: tuple = (255, 245, 190)
    fps: float = 10.0
    mirror: bool = True
    only_when_active: bool = True
    device: str = ""

    @classmethod
    def from_cfg(cls, cfg: dict) -> "CameraSettings":
        c = cfg.get("camera", {})
        d = cls()
        return cls(
            enabled=bool(c.get("enabled", d.enabled)),
            tint=tuple(float(v) for v in c.get("tint", d.tint)),
            head_tint=tuple(float(v) for v in c.get("head_tint", d.head_tint)),
            fade=max(0.05, float(c.get("fade", d.fade))),
            fade_curve=max(0.1, float(c.get("fade_curve", d.fade_curve))),
            idle_after=max(0.0, float(c.get("idle_after", d.idle_after))),
            idle_fade=max(0.05, float(c.get("idle_fade", d.idle_fade))),
            gain=max(0.1, float(c.get("gain", d.gain))),
            sensitivity=min(1.0, max(0.0, float(c.get("sensitivity", d.sensitivity)))),
            strength=min(1.0, max(0.0, float(c.get("strength", d.strength)))),
            fill=min(1.0, max(0.0, float(c.get("fill", d.fill)))),
            warmup=max(0.0, float(c.get("warmup", d.warmup))),
            shape="fill" if str(c.get("shape", d.shape)).lower() == "fill" else "outline",
            outline_width=max(1, min(8, int(c.get("outline_width", d.outline_width)))),
            burst=max(0.0, float(c.get("burst", d.burst))),
            burst_rest=max(0.0, float(c.get("burst_rest", d.burst_rest))),
            burst_strength=max(1.0, float(c.get("burst_strength", d.burst_strength))),
            burst_tint=tuple(float(v) for v in c.get("burst_tint", d.burst_tint)),
            burst_head_tint=tuple(float(v) for v in c.get("burst_head_tint", d.burst_head_tint)),
            fps=max(1.0, float(c.get("fps", d.fps))),
            mirror=bool(c.get("mirror", d.mirror)),
            only_when_active=bool(c.get("only_when_active", d.only_when_active)),
            device=str(c.get("device", d.device)),
        )


# ------------------------------------------------------------------ sources

class Source:
    """Frame source interface.

    open(); read() -> a grayscale 'L' Image the first time a new frame is
    available, None otherwise (a frame is returned only once); close().
    After a successful read, ``last_time`` is that frame's arrival time
    (time.monotonic seconds).
    """

    name = "source"
    last_time: float = 0.0

    def open(self) -> None:
        raise NotImplementedError

    def read(self) -> Image.Image | None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class SyntheticSource(Source):
    """A head-and-shoulders figure drifting slowly side to side and up and down.

    About 40% of the frame tall, standing at the bottom edge.  The figure is
    filled with fine stripes whose phase moves every frame, so the motion mask
    covers its interior as well as its outline, the way a real person's small
    movements do.
    """

    name = "synthetic"
    STRIPE = 8  # stripe period in pixels; the phase moves half a period per frame

    def __init__(self, width: int = CAM_W, height: int = CAM_H, clock=time.monotonic, period: float = 8.0,
                 fps: float = 10.0):
        self.w, self.h = width, height
        self.clock = clock
        self.period = period
        self.fps = max(1.0, fps)
        self.interval = 1.0 / self.fps
        self.t0 = None
        self._stripes = []
        for phase in range(self.STRIPE):
            img = Image.new("L", (width, height), 150)
            d = ImageDraw.Draw(img)
            for y in range(-self.STRIPE, height + self.STRIPE, self.STRIPE):
                d.rectangle((0, y + phase, width, y + phase + self.STRIPE // 2 - 1), fill=215)
            self._stripes.append(img)

    def open(self) -> None:
        self.t0 = self.clock()
        self.last_time = 0.0

    def figure_mask(self, t: float) -> Image.Image:
        w, h = self.w, self.h
        mask = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(mask)
        cx = w * 0.5 + w * 0.22 * math.sin(2 * math.pi * t / self.period)
        dy = h * 0.025 * math.sin(2 * math.pi * t / (self.period * 0.6))
        # Torso: a wide ellipse whose top forms the shoulders, cut by the frame bottom.
        tw, top = w * 0.44, h * 0.74 + dy
        d.ellipse((cx - tw / 2, top, cx + tw / 2, top + h * 0.55), fill=255)
        # Neck and head.
        d.rectangle((cx - w * 0.035, top - h * 0.04, cx + w * 0.035, top + h * 0.03), fill=255)
        hw, hh = w * 0.10, h * 0.20
        hy = top - h * 0.03 - hh / 2
        d.ellipse((cx - hw / 2, hy - hh / 2, cx + hw / 2, hy + hh / 2), fill=255)
        return mask

    def frame_at(self, t: float) -> Image.Image:
        w, h = self.w, self.h
        img = Image.new("L", (w, h), 28)
        draw = ImageDraw.Draw(img)
        # A little fixed room detail so the background is not flat.
        draw.rectangle((int(w * 0.05), int(h * 0.1), int(w * 0.18), int(h * 0.9)), fill=60)
        frame_no = int(round(t * self.fps))
        stripes = self._stripes[(frame_no * (self.STRIPE // 2)) % self.STRIPE]
        img.paste(stripes, (0, 0), self.figure_mask(t))
        return img

    def read(self) -> Image.Image | None:
        """A new frame at most every 1/fps seconds, like the camera; None in between."""
        if self.t0 is None:
            return None
        now = self.clock()
        if self.last_time and now - self.last_time < self.interval:
            return None
        self.last_time = now
        return self.frame_at(now - self.t0)

    def close(self) -> None:
        self.t0 = None


class CameraUnavailable(Exception):
    pass


_OWNERS: dict = {}
_DELEGATE_CLASS = None


def _delegate_class():
    """The sample-buffer delegate class, defined once per process."""
    global _DELEGATE_CLASS
    if _DELEGATE_CLASS is None:
        from Foundation import NSObject

        class MatrixRainCameraDelegate(NSObject):
            def captureOutput_didOutputSampleBuffer_fromConnection_(self, _out, sample, _conn):
                owner = _OWNERS.get(id(self))
                if owner is not None:
                    owner._on_sample(sample)

        _DELEGATE_CLASS = MatrixRainCameraDelegate
    return _DELEGATE_CLASS


class CameraSource(Source):
    """The Mac's camera through AVFoundation, low resolution, luma plane only."""

    name = "camera"

    def __init__(self, device: str = "", fps: float = 10.0):
        self.device_name = device
        self.fps = fps
        self.session = None
        self.delegate = None
        self.queue = None
        self.lock = threading.Lock()
        self.latest: tuple | None = None  # (width, height, stride, bytes, arrival time)
        self.seq = 0            # frames stored by the delegate
        self.returned_seq = 0   # last frame handed out by read()
        self.errors = 0
        self.last_error = ""
        self.dropped = 0
        self.min_interval = 1.0 / max(1.0, fps)
        self._last_accept = None
        self.rate_note = ""

    def open(self) -> None:
        try:
            import AVFoundation as AV
            import Quartz
            from libdispatch import dispatch_queue_create
        except Exception as exc:  # noqa: BLE001
            raise CameraUnavailable(
                f"camera packages not installed ({exc.__class__.__name__}); run ./install.sh --camera") from exc

        media = AV.AVMediaTypeVideo
        status = AV.AVCaptureDevice.authorizationStatusForMediaType_(media)
        if status in (1, 2):  # restricted, denied
            raise CameraUnavailable("camera access is denied in System Settings > Privacy & Security > Camera")

        device = None
        if self.device_name:
            types = [AV.AVCaptureDeviceTypeBuiltInWideAngleCamera]
            for extra in ("AVCaptureDeviceTypeExternal", "AVCaptureDeviceTypeContinuityCamera"):
                if hasattr(AV, extra):
                    types.append(getattr(AV, extra))
            found = AV.AVCaptureDeviceDiscoverySession.discoverySessionWithDeviceTypes_mediaType_position_(
                types, media, 0).devices()
            device = next((d for d in found if self.device_name.lower() in str(d.localizedName()).lower()), None)
            if device is None:
                raise CameraUnavailable(f"no camera named like {self.device_name!r}")
        else:
            device = AV.AVCaptureDevice.defaultDeviceWithMediaType_(media)
        if device is None:
            raise CameraUnavailable("no camera found")

        dev_input, err = AV.AVCaptureDeviceInput.deviceInputWithDevice_error_(device, None)
        if dev_input is None:
            raise CameraUnavailable(f"cannot open camera: {err}")
        session = AV.AVCaptureSession.alloc().init()
        for preset in ("AVCaptureSessionPreset320x240", "AVCaptureSessionPresetLow"):
            value = getattr(AV, preset, None)
            if value is not None and session.canSetSessionPreset_(value):
                session.setSessionPreset_(value)
                break
        if not session.canAddInput_(dev_input):
            raise CameraUnavailable("camera input rejected")
        session.addInput_(dev_input)
        self.rate_note = self._limit_frame_rate(AV, device)

        output = AV.AVCaptureVideoDataOutput.alloc().init()
        output.setAlwaysDiscardsLateVideoFrames_(True)
        # 420f: plane 0 is full-range luma, which is the grayscale image.
        output.setVideoSettings_({Quartz.kCVPixelBufferPixelFormatTypeKey: 0x34323066})
        self.delegate = _delegate_class().alloc().init()
        _OWNERS[id(self.delegate)] = self
        self.queue = dispatch_queue_create(b"iterm2-matrix-rain.camera", None)
        output.setSampleBufferDelegate_queue_(self.delegate, self.queue)
        if not session.canAddOutput_(output):
            raise CameraUnavailable("camera output rejected")
        session.addOutput_(output)
        session.startRunning()
        self.session = session

    def _on_sample(self, sample) -> None:
        try:
            import CoreMedia
            import Quartz
            pb = CoreMedia.CMSampleBufferGetImageBuffer(sample)
            Quartz.CVPixelBufferLockBaseAddress(pb, 1)
            try:
                w = Quartz.CVPixelBufferGetWidthOfPlane(pb, 0)
                h = Quartz.CVPixelBufferGetHeightOfPlane(pb, 0)
                stride = Quartz.CVPixelBufferGetBytesPerRowOfPlane(pb, 0)
                base = Quartz.CVPixelBufferGetBaseAddressOfPlane(pb, 0)
                data = bytes(base.as_buffer(stride * h))
            finally:
                Quartz.CVPixelBufferUnlockBaseAddress(pb, 1)
            self._store(w, h, stride, data, time.monotonic())
        except Exception as exc:  # noqa: BLE001 - a dropped frame is fine, but count it
            with self.lock:
                self.errors += 1
                self.last_error = f"{exc.__class__.__name__}: {exc}"

    def _limit_frame_rate(self, AV, device) -> str:
        """Ask the camera for the configured rate.  Returns a note for the log.

        If the device refuses, _store() still drops frames so at most `fps`
        per second reach the motion mask.
        """
        try:
            import CoreMedia

            fps = int(round(1.0 / self.min_interval))
            ranges = device.activeFormat().videoSupportedFrameRateRanges()
            if not any(r.minFrameRate() <= fps <= r.maxFrameRate() for r in ranges):
                return f"camera cannot run at {fps} fps; dropping frames to {fps} fps instead"
            ok, err = device.lockForConfiguration_(None)
            if not ok:
                return f"camera rate locked ({err}); dropping frames to {fps} fps instead"
            try:
                duration = CoreMedia.CMTimeMake(1, fps)
                device.setActiveVideoMinFrameDuration_(duration)
                device.setActiveVideoMaxFrameDuration_(duration)
            finally:
                device.unlockForConfiguration()
            return f"camera set to {fps} fps"
        except Exception as exc:  # noqa: BLE001
            return f"camera rate not set ({exc.__class__.__name__}); dropping frames instead"

    def _store(self, w: int, h: int, stride: int, data: bytes, t: float) -> None:
        """Called on the capture queue for each frame (and by tests).

        Frames closer together than 1/fps (with 10% slack) are dropped, so a
        camera that ignores the requested rate still feeds at most `fps`.
        """
        with self.lock:
            if self._last_accept is not None and t - self._last_accept < self.min_interval * 0.9:
                self.dropped += 1
                return
            self._last_accept = t
            self.latest = (w, h, stride, data, t)
            self.seq += 1

    def stats(self) -> dict:
        with self.lock:
            return {"delivered": self.seq, "dropped": self.dropped, "errors": self.errors,
                    "last_error": self.last_error}

    def read(self) -> Image.Image | None:
        with self.lock:
            if self.latest is None or self.seq == self.returned_seq:
                return None
            self.returned_seq = self.seq
            latest = self.latest
        w, h, stride, data, t = latest
        self.last_time = t
        img = Image.frombuffer("L", (w, h), data, "raw", "L", stride, 1)
        if (w, h) != (CAM_W, CAM_H):
            img = img.resize((CAM_W, CAM_H), Image.Resampling.BILINEAR)
        return img

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.stopRunning()
            except Exception:  # noqa: BLE001
                pass
        self.session = None
        if self.delegate is not None:
            _OWNERS.pop(id(self.delegate), None)
        self.delegate = None
        self.queue = None
        with self.lock:
            self.latest = None
            self.returned_seq = self.seq
            self._last_accept = None


# ------------------------------------------------------------------ motion

class MotionField:
    """Turns successive grayscale frames into a motion mask at camera size."""

    # The background follows the scene with this time constant (seconds), so
    # something that stops moving fades out of the mask in about a second.
    BG_TAU = 0.6

    def __init__(self, settings: CameraSettings):
        self.s = settings
        self.bg: Image.Image | None = None
        self.mask: Image.Image | None = None
        self.prev: Image.Image | None = None
        self.last_frame_diff = 0.0   # mean |frame - previous frame|, gray levels
        # sensitivity 0 -> 44 gray levels, 0.5 -> 24, 1 -> 4.  A waving hand at
        # 10 fps differs from the background by tens of levels; sensor noise
        # is a few.
        self.threshold = int(round(4 + (1.0 - settings.sensitivity) * 40))
        self.elapsed = 0.0           # seconds of frames seen since open/reset
        self.raw_mask: Image.Image | None = None   # what feeds the heat (outline or fill)
        self.filled: Image.Image | None = None     # everything that moved
        self._thr_lut = [255 if v > self.threshold else 0 for v in range(256)]

    def reset(self) -> None:
        self.bg = None
        self.mask = None
        self.prev = None
        self.last_frame_diff = 0.0
        self.elapsed = 0.0
        self.raw_mask = None
        self.filled = None

    def feed(self, frame: Image.Image, dt: float = 0.1) -> Image.Image:
        """Add one new frame; dt is the time since the previous frame."""
        if frame.mode != "L":
            frame = frame.convert("L")
        if frame.size != (CAM_W, CAM_H):
            frame = frame.resize((CAM_W, CAM_H), Image.Resampling.BILINEAR)
        if self.s.mirror:
            frame = frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if self.bg is None:
            self.bg = frame
        diff = ImageChops.difference(frame, self.bg)
        moving = diff.point(self._thr_lut)
        if self.prev is not None:
            step = ImageChops.difference(frame, self.prev)
            self.last_frame_diff = _mean(step)
            # Gate: keep only places changing right now (frame to frame, grown
            # a few pixels), so the spot someone just left, which still differs
            # from the slow background, does not leave a ghost.
            half = step.resize((CAM_W // 2, CAM_H // 2), Image.Resampling.BOX).point(self._thr_lut)
            gate = half.filter(ImageFilter.MaxFilter(5)).resize((CAM_W, CAM_H), Image.Resampling.NEAREST)
            moving = ImageChops.darker(moving, gate)
        self.prev = frame
        moving = moving.filter(ImageFilter.GaussianBlur(1.5))
        self.filled = moving
        self.raw_mask = outline(moving, self.s.outline_width) if self.s.shape == "outline" else moving
        if self.bg is not frame:
            self.elapsed += max(0.0, min(dt, 1.0))
        # Warm-up gate: while the background settles after the camera opens,
        # nearly everything differs from it.  No heat is applied until then.
        self.mask = None if self.warming else self.raw_mask
        rate = 1.0 - math.exp(-max(0.0, min(dt, 1.0)) / self.BG_TAU)
        self.bg = Image.blend(self.bg, frame, rate)
        return self.mask

    @property
    def warming(self) -> bool:
        return self.elapsed < self.s.warmup

    def coverage(self) -> float:
        """Share of the camera frame that moved (0..1), warm-up included."""
        if self.filled is None:
            return 0.0
        hist = self.filled.histogram()
        return sum(hist[128:]) / float(CAM_W * CAM_H)


_BIN = [255 if v >= 96 else 0 for v in range(256)]


def solid_blob(mask: Image.Image) -> Image.Image:
    """Binarize at half size, close small holes (dilate then erode), drop specks."""
    half = mask.resize((mask.width // 2, mask.height // 2), Image.Resampling.BOX).point(_BIN)
    half = half.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))   # close
    half = half.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))   # open: drop specks
    return half


def outline(mask: Image.Image, width: int = 3) -> Image.Image:
    """The edge of the moving shape: blob minus its erosion.

    Worked at half size; `width` is the edge thickness in half-size pixels
    (about 2 * width camera pixels once scaled back), enough to survive the
    downsample to rain cells, at a cost of about 1 ms per camera frame.
    """
    blob = solid_blob(mask)
    k = 2 * max(1, int(width)) + 1
    edge = ImageChops.subtract(blob, blob.filter(ImageFilter.MinFilter(k)))
    return edge.resize(mask.size, Image.Resampling.BILINEAR)


class Burst:
    """Makes the tint stronger for the first `burst` seconds of new motion.

    Motion counts as new when it starts after at least `rest` seconds of no
    motion.  factor() is 1.0 during the burst, then eases to 0 over `ease`
    seconds; continuous motion never starts a second burst.
    """

    FLOOR = 0.002   # share of the frame that must move to count as motion

    def __init__(self, burst: float = 3.0, rest: float = 2.0, ease: float = 1.0):
        self.burst, self.rest, self.ease = burst, rest, ease
        self.last_motion: float | None = None
        self.start: float | None = None

    def update(self, coverage: float, now: float) -> None:
        if coverage <= self.FLOOR:
            return
        quiet = self.last_motion is None or now - self.last_motion >= self.rest
        if quiet and self.burst > 0:
            self.start = now
        self.last_motion = now

    def idle_for(self, now: float) -> float:
        """Seconds since motion was last seen (inf before any motion)."""
        return float("inf") if self.last_motion is None else now - self.last_motion

    def factor(self, now: float) -> float:
        if self.start is None:
            return 0.0
        e = now - self.start
        if e < self.burst:
            return 1.0
        if e < self.burst + self.ease:
            x = (e - self.burst) / self.ease
            return 1.0 - x * x * (3 - 2 * x)
        return 0.0

    def reset(self) -> None:
        self.last_motion = None
        self.start = None


def _mean(img: Image.Image) -> float:
    hist = img.histogram()
    total = sum(hist)
    return sum(i * n for i, n in enumerate(hist)) / total if total else 0.0


class Heat:
    """Per-rain-cell heat for one grid: rises at once, fades over `fade` s."""

    def __init__(self, ncols: int, nrows: int, fade: float, dilate: bool = True):
        self.size = (max(1, ncols), max(1, nrows))
        self.fade = fade
        self.dilate = dilate   # grow by one cell (fill mode); outlines stay thin
        self.img = Image.new("L", self.size, 0)

    def resize(self, ncols: int, nrows: int) -> None:
        self.size = (max(1, ncols), max(1, nrows))
        self.img = Image.new("L", self.size, 0)

    def update(self, mask: Image.Image | None, dt: float, fade: float | None = None) -> Image.Image:
        """Decay by dt over `fade` seconds (the instance's fade unless overridden,
        e.g. the fast idle_fade once motion has stopped), then merge new motion."""
        drop = int(round(255.0 * max(0.0, dt) / (fade or self.fade)))
        if drop:
            lut = [max(0, v - drop) for v in range(256)]
            self.img = self.img.point(lut)
        if mask is not None:
            cells = mask.resize(self.size, Image.Resampling.BOX)
            if self.dilate:
                cells = cells.filter(ImageFilter.MaxFilter(3))
            # Normalize: a cell half covered by motion counts as fully moving.
            cells = cells.point([min(255, v * 2) for v in range(256)])
            self.img = ImageChops.lighter(self.img, cells)
        return self.img

    def peak(self) -> int:
        return self.img.getextrema()[1]


def shape_heat(heat: Image.Image, curve: float) -> Image.Image:
    """Map linear heat through a power curve so fresh motion stands out:
    with curve > 1, half-faded cells are already much dimmer than new ones."""
    if abs(curve - 1.0) < 1e-6:
        return heat
    lut = [int(round(255.0 * (v / 255.0) ** curve)) for v in range(256)]
    return heat.point(lut)


@dataclass
class Tint:
    """What the renderer needs to tint moving cells."""

    heat: Image.Image          # 'L', one pixel per rain cell
    tail: tuple
    head: tuple
    strength: float = 1.0
    fill: float = 0.0          # dim glyph level drawn in moving cells with no rain
    gain: float = 1.0          # brightness multiplier for tinted cells (burst)

    @classmethod
    def from_settings(cls, heat: Image.Image, s: "CameraSettings", burst: float = 0.0) -> "Tint":
        """The tint for a burst factor b in 0..1: stronger colors, full
        strength and brighter cells at b = 1, the normal settings at b = 0."""
        b = min(1.0, max(0.0, burst))

        def mix(a, c):
            return tuple(x + (y - x) * b for x, y in zip(a, c))

        heat = shape_heat(heat, s.fade_curve)
        return cls(heat, mix(s.tint, s.burst_tint), mix(s.head_tint, s.burst_head_tint),
                   s.strength + (1.0 - s.strength) * b, s.fill,
                   s.gain * (1.0 + (s.burst_strength - 1.0) * b))
