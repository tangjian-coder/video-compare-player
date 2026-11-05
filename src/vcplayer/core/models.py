"""Data models for video-compare-player."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Orientation = Literal["portrait", "landscape"]


def display_orientation(width: float, height: float, rotation: float = 0.0) -> Orientation | None:
    """Classify the displayed orientation after rotation metadata is applied."""
    if (
        not math.isfinite(width)
        or not math.isfinite(height)
        or not math.isfinite(rotation)
        or width <= 0.0
        or height <= 0.0
    ):
        return None
    if round(rotation) % 180:
        width, height = height, width
    return "portrait" if height > width else "landscape"


@dataclass(frozen=True)
class VideoMeta:
    """Static metadata of a loaded video file."""

    path: Path
    duration: float  # seconds
    fps: float
    width: int
    height: int


@dataclass
class SyncState:
    """Sync points in each view's own timeline (seconds)."""

    point_a: float | None = None
    point_b: float | None = None

    @property
    def is_complete(self) -> bool:
        """True when both views have a sync point set."""
        return self.point_a is not None and self.point_b is not None

    @property
    def offset(self) -> float:
        """B-timeline shift relative to A: t_b = t_a + offset."""
        if not self.is_complete:
            return 0.0
        assert self.point_a is not None and self.point_b is not None
        return self.point_b - self.point_a
