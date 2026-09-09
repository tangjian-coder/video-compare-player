"""Sync engine: aligns two player timelines.

Master time is view A's timeline. View B is shifted by the sync offset:
    t_b = t_a + offset,  offset = sync_point_b - sync_point_a

Qt-free and unit-testable; depends on players only through the PlayerLike
protocol.
"""

from __future__ import annotations

import logging
from typing import Literal, Protocol

from .models import SyncState

logger = logging.getLogger(__name__)

ViewId = Literal["a", "b"]

# regulate() tuning constants (measured; see regulate docstring).
_EWMA_ALPHA = 0.3  # smoothing factor for drift readings
_MAX_NUDGE = 0.10  # speed correction limit (+/- 10%)
_FULL_NUDGE_DRIFT = 0.03  # drift (s) that saturates the nudge
_SPEED_WRITE_EPS = 0.005  # minimum meaningful speed change to write

# resync_if_needed() gate: drift beyond this many reference frames gets a
# corrective seek; smaller residuals are converged by the speed loop.
_RESYNC_DRIFT_FRAMES = 1.2


class SyncError(RuntimeError):
    """Raised when time information is unavailable (no file loaded)."""


class PlayerLike(Protocol):
    """Minimal player interface required by SyncEngine."""

    @property
    def time_pos(self) -> float | None: ...

    @property
    def duration(self) -> float | None: ...

    @property
    def fps(self) -> float | None: ...

    def seek_exact(self, t: float) -> None: ...

    def seek_fast(self, t: float) -> None: ...

    def pause(self) -> None: ...

    def set_speed(self, speed: float) -> None: ...


class SyncEngine:
    """Keeps two players time-aligned via a sync-point offset model."""

    def __init__(self, a: PlayerLike, b: PlayerLike) -> None:
        self.a = a
        self.b = b
        self.state = SyncState()
        self._last_b_speed: float | None = None
        self._drift_ewma: float | None = None

    # -- sync points ----------------------------------------------------------

    @property
    def offset(self) -> float:
        """Current offset: t_b = t_a + offset."""
        return self.state.offset

    def sync_here(self) -> float:
        """Align on the two current frames: both become the sync points.

        One-shot replacement for the old two-step point-by-point workflow.
        Returns the resulting offset.
        """
        self.state.point_a = self._require_time(self.a)
        self.state.point_b = self._require_time(self.b)
        self._reset_runtime()
        logger.info(
            "synced here: a=%.3f b=%.3f offset=%+.3f",
            self.state.point_a,
            self.state.point_b,
            self.offset,
        )
        return self.offset

    def clear_sync(self) -> None:
        """Drop both sync points (offset returns to 0).

        Resets in place so external references to `state` stay valid.
        """
        self.state.point_a = None
        self.state.point_b = None
        self._reset_runtime()
        logger.info("sync cleared")

    # -- time mapping ---------------------------------------------------------

    @property
    def ready(self) -> bool:
        """True when both players have a usable duration."""
        return bool(
            self.a.duration and self.a.duration > 0 and self.b.duration and self.b.duration > 0
        )

    @property
    def reference_fps(self) -> float:
        """Frame rate of the reference view (A); fallback 25 fps."""
        fps = self.a.fps
        return fps if fps and fps > 0 else 25.0

    def master_time(self) -> float:
        """Current master position (view A's timeline)."""
        return self._require_time(self.a)

    def master_range(self) -> tuple[float, float]:
        """Valid master interval where both views have frames."""
        dur_a = self._require_duration(self.a)
        dur_b = self._require_duration(self.b)
        lo = max(0.0, -self.offset)
        hi = min(dur_a, dur_b - self.offset)
        return lo, max(lo, hi)

    def view_time(self, view: ViewId, master: float) -> float:
        """Map a master position to a view-local time."""
        return master if view == "a" else master + self.offset

    def seek_master(self, master: float, *, exact: bool = True) -> float:
        """Seek both views to a master position; returns the clamped position.

        exact=False uses keyframe-fast seeks: coarse but cheap, for scrubbing.
        """
        lo, hi = self.master_range()
        m = min(max(master, lo), hi)
        if exact:
            self.a.seek_exact(m)
            self.b.seek_exact(m + self.offset)
        else:
            self.a.seek_fast(m)
            self.b.seek_fast(m + self.offset)
        return m

    def force_resync(self) -> None:
        """Hard-align B onto A's aligned time right now (startup stabilizer).

        B's target is clamped to its own stream: when A sits outside the
        common range a perfectly aligned B position may not exist, and an
        explicit clamp (unlike mpv's silent one) keeps the seek deliberate.
        """
        target = self._require_time(self.a) + self.offset
        dur_b = self.b.duration
        if dur_b is not None:
            target = min(max(target, 0.0), dur_b)
        self.b.seek_exact(target)
        logger.info("force resync: b -> %.3f (offset %+.3f)", target, self.offset)

    def resync_if_needed(self) -> bool:
        """Hard-align B onto A only when drift exceeds the frame gate.

        Returns True when a corrective seek was issued. An unconditional
        seek restarts B's decode pipeline (keyframe backstep plus decode
        forward), stalling B for hundreds of ms on long-GOP sources while
        A already plays — so seek only when meaningfully misaligned and
        leave sub-frame residuals to regulate()'s speed loop.
        """
        drift = self.drift()
        if drift is None or abs(drift) <= _RESYNC_DRIFT_FRAMES / self.reference_fps:
            return False
        self.force_resync()
        return True

    def step(self, frames: int) -> float:
        """Pause and step both views by N reference frames; returns new master."""
        self.a.pause()
        self.b.pause()
        return self.seek_master(self.master_time() + frames / self.reference_fps)

    # -- drift handling ---------------------------------------------------------

    def drift(self) -> float | None:
        """Signed drift in seconds; positive means A is ahead of B."""
        t_a, t_b = self.a.time_pos, self.b.time_pos
        if t_a is None or t_b is None:
            return None
        return t_a - (t_b - self.offset)

    def regulate(self, base_speed: float) -> float | None:
        """Continuously steer B onto A's aligned time via speed nudges.

        Design constraints, all measured:
        - never hard-seek during playback (a seek restarts B's pipeline and
          induces its own ~40 ms skew — the correction becomes the noise);
        - drift readings are EWMA-smoothed (raw reads carry display-quantum
          jitter, which would otherwise drive constant speed churn);
        - speed writes only on meaningful change (each write costs B a
          ~1 ms hiccup).
        Returns the smoothed drift in seconds, or None if unavailable.
        """
        raw = self.drift()
        if raw is None:
            return None
        self._drift_ewma = (
            raw
            if self._drift_ewma is None
            else (1.0 - _EWMA_ALPHA) * self._drift_ewma + _EWMA_ALPHA * raw
        )
        d = self._drift_ewma
        fps_b = self.b.fps if self.b.fps and self.b.fps > 0 else self.reference_fps
        # One frame of B: below this is frame-grid noise. (Requires the
        # deadband to stay above _SPEED_WRITE_EPS in rate terms, which holds
        # for any realistic fps — a 1-frame deadband only shrinks below the
        # write gate beyond ~666 fps.)
        deadband = 1.0 / fps_b
        if abs(d) > deadband:
            rate = max(-_MAX_NUDGE, min(_MAX_NUDGE, d / _FULL_NUDGE_DRIFT * _MAX_NUDGE))
            target = base_speed * (1.0 + rate)
        else:
            target = base_speed
        written = self._set_b_speed(target)
        # Debug trace: intermittent drift-growth reports need a record of
        # what the loop actually did, tick by tick (--debug only).
        logger.debug(
            "regulate: raw=%+.4f ewma=%+.4f target=%.4f %s",
            raw,
            d,
            target,
            "write" if written else "keep",
        )
        return d

    def _set_b_speed(self, speed: float) -> bool:
        """Write B's speed only on meaningful change.

        Measured: every speed write costs B a ~1 ms playback hiccup, so
        rewriting an unchanged value every tick measurably drives drift.
        Returns True when the speed was actually written.
        """
        if self._last_b_speed is not None and abs(speed - self._last_b_speed) < _SPEED_WRITE_EPS:
            return False
        self.b.set_speed(speed)
        self._last_b_speed = speed
        return True

    def _reset_runtime(self) -> None:
        """Forget regulate() state after the offset basis changes.

        A stale EWMA / speed cache would steer B by the previous offset's
        drift for ~7 ticks until the smoothing converges again.
        """
        self._drift_ewma = None
        self._last_b_speed = None

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _require_time(p: PlayerLike) -> float:
        t = p.time_pos
        if t is None:
            raise SyncError("time unavailable (no file loaded)")
        return t

    @staticmethod
    def _require_duration(p: PlayerLike) -> float:
        d = p.duration
        if d is None:
            raise SyncError("duration unavailable (no file loaded)")
        return d
