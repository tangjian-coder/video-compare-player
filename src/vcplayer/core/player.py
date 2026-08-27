"""Single-view player controller backed by libmpv.

Wraps one libmpv instance embedded in a native window handle (wid).
All times are in this view's own timeline (seconds). No sync logic here.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

from .models import VideoMeta

logger = logging.getLogger(__name__)


class PlayerError(RuntimeError):
    """Raised when libmpv is unavailable or a file cannot be loaded."""


class PlayerController:
    """Controls one embedded libmpv instance."""

    def __init__(self, wid: int) -> None:
        """Create an mpv instance rendering into the given native window id."""
        try:
            import mpv
        except (OSError, ImportError) as exc:
            raise PlayerError(
                f"libmpv-2.dll not found ({exc}). Ensure vendor/ is on PATH."
            ) from exc

        self._mpv: Any = mpv.MPV(
            wid=str(wid),
            idle=True,
            keep_open=True,
            pause=True,
            hwdec="auto-safe",
            hr_seek=True,
            aid="no",
            osc=False,
            osd_level=0,
            input_default_bindings=False,
            input_vo_keyboard=False,
            log_handler=self._on_mpv_log,
            loglevel="warn",
        )
        self._meta: VideoMeta | None = None
        self._terminated = False
        # Display rotation in degrees (0/90/180/270). Tracked locally:
        # rotating through the property avoids round-trip reads entirely.
        self._rotation = 0

    def _on_mpv_log(self, loglevel: str, component: str, message: str) -> None:
        """Forward mpv log messages to the logging module."""
        logger.debug("[mpv:%s] %s", component, message.rstrip())

    def try_capture_meta(self) -> VideoMeta | None:
        """Poll-based metadata capture; returns meta once the file is demuxed."""
        if self._meta is not None:
            return self._meta
        if self._terminated:
            return None
        try:
            duration, fps = self.duration, self.fps
            params = self._mpv.video_params
            width = params.get("w") if isinstance(params, dict) else None
            height = params.get("h") if isinstance(params, dict) else None
            path = self._mpv.path
        except (AttributeError, TypeError, ValueError, RuntimeError, SystemError):
            # Not demuxed yet (or the core died); poll again on the next tick.
            logger.debug("metadata not ready", exc_info=True)
            return None
        if duration and fps and width and height and path:
            self._meta = VideoMeta(
                path=Path(path),
                duration=float(duration),
                fps=float(fps),
                width=int(width),
                height=int(height),
            )
            logger.info("loaded: %s", self._meta)
        return self._meta

    # -- properties ---------------------------------------------------------

    @property
    def meta(self) -> VideoMeta | None:
        """Video metadata, available once the file-loaded event fired."""
        return self._meta

    @property
    def time_pos(self) -> float | None:
        """Current playback position in seconds, None if unavailable."""
        if self._terminated:
            return None
        v = self._mpv.time_pos
        return float(v) if v is not None else None

    @property
    def duration(self) -> float | None:
        """Stream duration in seconds, None if unavailable."""
        if self._terminated:
            return None
        v = self._mpv.duration
        return float(v) if v is not None else None

    @property
    def fps(self) -> float | None:
        """Video frame rate, None if unavailable.

        mpv 0.41 removed the legacy `fps` property; try current names.
        """
        if self._terminated:
            return None
        for name in ("container_fps", "estimated_frame_rate"):
            try:
                v = getattr(self._mpv, name)
            except AttributeError:
                continue
            if v:
                return float(v)
        return None

    @property
    def playback_time(self) -> float | None:
        """Clock-interpolated playback position.

        Unlike time_pos (quantized to frame updates), playback-time is
        interpolated from mpv's playback clock and advances smoothly between
        frames. None if unavailable.
        """
        if self._terminated:
            return None
        try:
            v = self._mpv.playback_time
        except AttributeError:
            return None
        return float(v) if v is not None else None

    @property
    def is_playing(self) -> bool:
        """True while not paused."""
        return not self._terminated and not bool(self._mpv.pause)

    @property
    def zoom(self) -> float:
        """Linear display zoom (1.0 = no zoom)."""
        if self._terminated:
            return 1.0
        return float(2.0 ** float(self._mpv.video_zoom))

    @property
    def rotation(self) -> int:
        """Display rotation in degrees (0/90/180/270), clockwise."""
        return self._rotation

    @property
    def pan(self) -> tuple[float, float]:
        """Pan offset as fractions of the scaled video size (x, y)."""
        if self._terminated:
            return (0.0, 0.0)
        return float(self._mpv.video_pan_x), float(self._mpv.video_pan_y)

    # -- commands -----------------------------------------------------------

    def load(self, path: Path) -> None:
        """Load a video file (async; meta appears once demuxed)."""
        if not path.is_file():
            raise PlayerError(f"file not found: {path}")
        if self._terminated:
            return
        self._meta = None
        logger.info("loading %s", path)
        try:
            self._mpv.command("loadfile", str(path))
        except SystemError as exc:  # mpv wraps command failures in SystemError
            raise PlayerError(f"failed to load {path}: {exc}") from exc

    def play(self) -> None:
        """Resume playback."""
        if not self._terminated:
            self._mpv.pause = False

    def pause(self) -> None:
        """Pause playback."""
        if not self._terminated:
            self._mpv.pause = True

    def toggle_play(self) -> None:
        """Toggle play/pause."""
        if not self._terminated:
            self._mpv.pause = not self._mpv.pause

    @staticmethod
    def _seek_target(t: float) -> float | None:
        """Clamp a seek target to >= 0; None rejects non-finite input."""
        if not math.isfinite(t):
            logger.warning("ignoring non-finite seek target: %r", t)
            return None
        return max(0.0, t)

    def seek_exact(self, t: float) -> None:
        """Frame-exact absolute seek in seconds (blocking)."""
        target = self._seek_target(t)
        if target is not None and not self._terminated:
            self._mpv.command("seek", target, "absolute", "exact")

    def seek_fast(self, t: float) -> None:
        """Keyframe-fast absolute seek; coarse but cheap, for scrub preview."""
        target = self._seek_target(t)
        if target is not None and not self._terminated:
            self._mpv.command("seek", target, "absolute")

    def step_frames(self, frames: int) -> None:
        """Pause and step N frames on this view's own timeline (clamped)."""
        self.pause()
        fps = self.fps or (self._meta.fps if self._meta is not None else None) or 25.0
        t = self.time_pos
        if t is None:
            return
        target = t + frames / fps
        if self.duration is not None:
            target = min(target, self.duration)
        self.seek_exact(target)

    def set_speed(self, speed: float) -> None:
        """Set playback speed multiplier."""
        if not self._terminated:
            self._mpv.speed = speed

    def set_zoom(self, zoom: float) -> None:
        """Set linear display zoom; maps to mpv's log2 video-zoom."""
        if not self._terminated:
            self._mpv.video_zoom = math.log2(max(0.01, zoom))

    def set_pan(self, x: float, y: float) -> None:
        """Set pan in fractions of the scaled video size."""
        if not self._terminated:
            self._mpv.video_pan_x = x
            self._mpv.video_pan_y = y

    def reset_view(self) -> None:
        """Reset zoom, pan and rotation to defaults."""
        self._rotation = 0
        if not self._terminated:
            self._mpv.video_rotate = 0
        self.set_zoom(1.0)
        self.set_pan(0.0, 0.0)

    def rotate_cw(self) -> None:
        """Rotate the display 90 degrees clockwise.

        Pan resets to center: after a 90-degree turn the old pan position is
        semantically meaningless, and a re-clamped holdover lands somewhere
        unpredictable. Zoom survives unchanged.
        """
        if self._terminated:
            return
        self._rotation = (self._rotation + 90) % 360
        self._mpv.video_rotate = self._rotation
        self.set_pan(0.0, 0.0)

    def terminate(self) -> None:
        """Destroy the mpv instance; idempotent."""
        if self._terminated:
            return
        self._terminated = True
        try:
            self._mpv.terminate()
        except Exception:
            logger.exception("error terminating mpv")
