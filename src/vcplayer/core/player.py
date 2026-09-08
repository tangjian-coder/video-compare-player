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

# External OSD overlay id for the center crosshair (arbitrary; one
# namespace per libmpv client, so no clash with anything else).
_CROSSHAIR_OSD_ID = 5001
# Crosshair arm half-length in window pixels; one unit wide, pure red.
_CROSSHAIR_ARM_PX = 120

# mpv's osd-dimensions property reports the video rectangle inside the
# window in window pixels: {"w", "h", "par", "aspect", "ml", "mt", "mr",
# "mb"} where m* are the black margins around the video content.
OsdDims = dict[str, float]


def pane_center_from_osd(dims: OsdDims | None) -> tuple[float, float]:
    """Center of the mpv surface (pane) in OSD pixels.

    The crosshair is a fixed screen reference: it must NOT follow the
    video content when the user pans/zooms/rotates, so margins are
    ignored and (w/2, h/2) is the pane center.
    """
    if not isinstance(dims, dict):
        return (0.0, 0.0)
    w = float(dims.get("w", 0.0))
    h = float(dims.get("h", 0.0))
    if w <= 0.0 or h <= 0.0:
        return (0.0, 0.0)
    return (w / 2.0, h / 2.0)


def build_crosshair_ass(cx: float, cy: float, arm: int = _CROSSHAIR_ARM_PX) -> str:
    """One ASS line drawing a 1-unit-wide pure-red cross at (cx, cy).

    The caller passes res_x/res_y equal to the OSD size, so one PlayRes
    unit equals one window pixel and the cross is exactly 1 px wide at
    any window size. Both arms are drawn symmetrically around the drawing
    origin, so the cross center is the bbox center. \\pos compensation:
    on the embedded libass shipped with the vendored libmpv 0.41, \\an5
    is ignored and \\pos lands on the drawing bbox's bottom-right corner,
    shifting the cross up-left by exactly one arm; adding arm to both
    \\pos coordinates re-centers it. This anchoring was pixel-verified
    against the vendored dll — re-verify after any libmpv upgrade (e.g.
    toggle the crosshair on a uniform-color video and check the cross
    sits at the pane center). \\3a/\\4a kill style outline/shadow so
    only the red fill renders.
    """
    return (
        f"{{\\pos({cx + arm:.1f},{cy + arm:.1f})\\an5\\p1\\1c&H0000FF&\\3a&HFF&\\4a&HFF&}}"
        f"m {-arm} 0 l {arm} 0 l {arm} 1 l {-arm} 1 "
        f"m 0 {-arm} l 0 {arm} l 1 {arm} l 1 {-arm}"
    )


def _osd_dims_to_playres(dims: OsdDims | None) -> tuple[int, int]:
    """PlayRes matching the OSD canvas (1 unit = 1 window pixel)."""
    if isinstance(dims, dict):
        w = int(round(float(dims.get("w", 0))))
        h = int(round(float(dims.get("h", 0))))
        if w > 0 and h > 0:
            return (w, h)
    return (1920, 1080)  # idle fallback; overlay is off then anyway


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
        # Crosshair overlay is player-level state; tracked locally so a
        # reload can re-assert it without the UI asking again.
        self._crosshair = False
        # osd-dimensions changes on window resize/DPI change; observing
        # it lets the crosshair re-pin to the (possibly resized) pane
        # center. Pan/zoom/rotate change the margins but NOT the pane
        # center, so the fixed crosshair stays put.
        self._mpv.observe_property("osd-dimensions", self._on_osd_dims_changed)
        # Last pushed (w, h); lets the observer skip redundant pushes
        # during pan/zoom drags, which fire dims changes without moving
        # the pane center.
        self._last_cross_dims: tuple[int, int] | None = None

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
    def crosshair(self) -> bool:
        """Whether the center crosshair overlay is shown."""
        return self._crosshair

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
        # The OSD overlay normally survives a loadfile; re-assert anyway
        # so a core that dropped it cannot silently lose the crosshair.
        # Force-push: the (w, h) dedup cache would skip a same-size pane.
        if self._crosshair:
            self._last_cross_dims = None
            self.set_crosshair(True)

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

    def set_crosshair(self, enabled: bool) -> None:
        """Show or hide the red crosshair at the pane center.

        Rendered by libass in mpv's OSD layer: it stays fixed at the pane
        center while zoom/pan/rotate move the video underneath, and
        re-pins to the (possibly resized) pane center whenever
        osd-dimensions change. Never raises: an overlay failure is logged
        and ignored rather than breaking the toggle.
        """
        self._crosshair = enabled
        if self._terminated:
            return
        if enabled:
            self._push_crosshair_overlay()
        else:
            self._remove_crosshair_overlay()

    def _push_crosshair_overlay(self) -> None:
        """(Re)send the crosshair overlay at the current pane center."""
        if self._terminated or not self._crosshair:
            return
        try:
            dims: OsdDims | None = self._mpv.osd_dimensions
        except (AttributeError, RuntimeError, SystemError):
            dims = None
        cx, cy = pane_center_from_osd(dims)
        playres = _osd_dims_to_playres(dims)
        if playres == self._last_cross_dims:
            return  # pane size unchanged: the same push is a no-op
        try:
            self._mpv.command(
                "osd-overlay",
                id=_CROSSHAIR_OSD_ID,
                format="ass-events",
                data=build_crosshair_ass(cx, cy),
                res_x=playres[0],
                res_y=playres[1],
            )
            self._last_cross_dims = playres
        except (ValueError, RuntimeError, SystemError) as exc:
            logger.warning("crosshair overlay failed: %s", exc)

    def _remove_crosshair_overlay(self) -> None:
        """Clear the crosshair overlay; idempotent."""
        if self._terminated:
            return
        self._last_cross_dims = None
        try:
            # mpv 0.41 rejects format=none without a data parameter.
            self._mpv.command("osd-overlay", id=_CROSSHAIR_OSD_ID, format="none", data="")
        except (ValueError, RuntimeError, SystemError) as exc:
            logger.warning("crosshair overlay removal failed: %s", exc)

    def _on_osd_dims_changed(self, _name: str, _value: object) -> None:
        """osd-dimensions observer: re-pin the crosshair after resizes."""
        # Fires from mpv's event thread; command() is thread-safe there,
        # and _push dedups on (w, h) so pan/zoom drags cost nothing.
        if self._crosshair and not self._terminated:
            self._push_crosshair_overlay()

    def terminate(self) -> None:
        """Destroy the mpv instance; idempotent."""
        if self._terminated:
            return
        self._terminated = True
        try:
            self._mpv.terminate()
        except Exception:
            logger.exception("error terminating mpv")
