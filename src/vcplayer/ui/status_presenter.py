"""Pure formatting and smoothing helpers for the status/timeline presenters."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from ..core.sync import ViewId


@dataclass(frozen=True)
class StatusReadout:
    text: str
    tooltip: str


def drift_ms(drift: float | None) -> str:
    return f"{drift * 1000:+.1f}ms" if drift is not None else "n/a"


def format_live_status(
    master: float,
    offsets: dict[ViewId, float],
    drifts: dict[ViewId, float | None],
    fps: float,
    master_view: ViewId = "a",
) -> StatusReadout:
    """Status-bar readout: exact per-pair detail until it stops fitting."""
    label = master_view.upper()
    parts = [
        f"{label}{vid.upper()} {off:+.3f}s ({drift_ms(drifts.get(vid))})"
        for vid, off in offsets.items()
    ]
    if len(offsets) <= 1:
        off = next(iter(offsets.values()), 0.0)
        # Prefer the drift of the synced pair itself; fall back to any
        # ready engine when nothing is synced (keeps the old readout).
        synced_vid = next(iter(offsets), None)
        drift = drifts.get(synced_vid) if synced_vid is not None else None
        if synced_vid is None:
            drift = next(iter(drifts.values()), None)
        text = f"{label} {master:8.3f}s | offset {off:+.3f}s | drift {drift_ms(drift)} | {fps:g}fps"
        return StatusReadout(text=text, tooltip="")
    if len(offsets) == 2:
        text = f"{label} {master:8.3f}s | {' | '.join(parts)} | {fps:g}fps"
        return StatusReadout(text=text, tooltip="")
    # 3 synced pairs: one line, per-pair detail in the tooltip. The
    # worst-case figure only considers synced pairs.
    worst = max(
        (abs(d) for vid, d in drifts.items() if vid in offsets and d is not None),
        default=0.0,
    )
    text = (
        f"{label} {master:8.3f}s | {len(offsets)} synced pairs "
        f"| max drift {worst * 1000:+.1f}ms | {fps:g}fps"
    )
    return StatusReadout(text=text, tooltip=" | ".join(parts))


class PlayheadSmoother:
    """Interpolate between mpv frame updates so the playhead glides."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._raw: float | None = None
        self._anchor = 0.0
        self._mono = 0.0

    def update(self, raw: float, playing: bool, speed: float, duration: float | None) -> float:
        now = self._clock()
        if not playing or self._raw is None or raw != self._raw:
            self._raw = raw
            self._anchor = raw
            self._mono = now
            return raw
        est = self._anchor + (now - self._mono) * speed
        # Clamp to the video's own extent: a solo-playing A past the sync
        # overlap must keep moving its knob mid-track.
        if duration is not None:
            est = min(max(est, 0.0), duration)
        return est
