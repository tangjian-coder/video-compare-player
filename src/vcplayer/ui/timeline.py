"""Master timeline ruler widget with second/frame ticks and a playhead."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

SCRUB_PREVIEW_INTERVAL_S = 0.06  # min interval between scrub preview seeks

# Candidate label spacings (seconds); the smallest one that keeps labels
# MIN_TICK_PX apart is used, so long videos don't overlap labels or force
# thousands of draw calls per repaint.
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800)
MIN_TICK_PX = 64.0


class TimelineWidget(QWidget):
    """Horizontal ruler over the master time range; click/drag to seek.

    Scrubbing emits throttled `scrub_preview` signals (coarse seeks) and a
    single `seek_committed` on release (exact seek), so dragging stays fluid
    even with high-fps, high-bitrate sources.
    """

    scrub_preview = Signal(float)
    seek_committed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(42)
        self._lo = 0.0
        self._hi = 0.0
        self._time = 0.0
        self._fps = 25.0
        self._dragging = False
        self._last_preview = 0.0
        self._marker: float | None = None  # sync point position in master time

    def set_marker(self, t: float | None) -> None:
        """Show or clear the sync-point marker."""
        self._marker = t
        self.update()

    def set_range(self, lo: float, hi: float) -> None:
        """Set the displayed master time range."""
        self._lo, self._hi = lo, max(lo, hi)
        self.update()

    def set_fps(self, fps: float) -> None:
        """Set the frame rate used for frame ticks."""
        if fps > 0:
            self._fps = fps
        self.update()

    def set_time(self, t: float) -> None:
        """Move the playhead."""
        self._time = t
        self.update()

    def _x_to_time(self, x: float) -> float:
        span = self._hi - self._lo
        if span <= 0 or self.width() <= 0:
            return self._lo
        frac = min(1.0, max(0.0, x / self.width()))
        return self._lo + frac * span

    def _emit_preview(self, t: float, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_preview >= SCRUB_PREVIEW_INTERVAL_S:
            self._last_preview = now
            self.scrub_preview.emit(t)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start scrubbing; immediate preview at the press position."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._emit_preview(self._x_to_time(event.position().x()), force=True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Throttled coarse preview while scrubbing."""
        if self._dragging:
            self._emit_preview(self._x_to_time(event.position().x()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Commit an exact seek at the release position."""
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            self.seek_committed.emit(self._x_to_time(event.position().x()))

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint background, ticks and the playhead (macOS dark palette)."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, QColor(0x1C, 0x1C, 0x1E))
        span = self._hi - self._lo
        if span > 0 and w > 0:
            px_per_sec = w / span
            # Second ticks with labels; adaptive step keeps labels legible
            # and bounds draw cost for arbitrarily long ranges.
            step = next(
                (s for s in TICK_STEPS_S if s * px_per_sec >= MIN_TICK_PX),
                TICK_STEPS_S[-1],
            )
            painter.setPen(QPen(QColor(0x8E, 0x8E, 0x93)))
            t = math.ceil(self._lo / step) * step
            while t <= self._hi:
                x = int((t - self._lo) * px_per_sec)
                painter.drawLine(x, h - 14, x, h)
                painter.drawText(x + 3, 12, f"{t:.0f}s")
                t += step
            # Frame ticks when there is enough horizontal room.
            if px_per_sec / self._fps >= 4.0:
                painter.setPen(QPen(QColor(0x48, 0x48, 0x4A)))
                frame = int(self._lo * self._fps)
                while frame / self._fps <= self._hi:
                    x = int((frame / self._fps - self._lo) * px_per_sec)
                    painter.drawLine(x, h - 6, x, h)
                    frame += 1
            # Sync-point marker: system-yellow diamond + stem at the anchor.
            if self._marker is not None and self._lo <= self._marker <= self._hi:
                mx = (self._marker - self._lo) * px_per_sec
                painter.setPen(QPen(QColor(0xFF, 0xD6, 0x0A), 2))
                painter.setBrush(QColor(0xFF, 0xD6, 0x0A))
                painter.drawLine(QPointF(mx, h - 12.0), QPointF(mx, float(h)))
                painter.drawPolygon(
                    [
                        QPointF(mx, h - 20.0),
                        QPointF(mx + 5.0, h - 13.0),
                        QPointF(mx, h - 6.0),
                        QPointF(mx - 5.0, h - 13.0),
                    ]
                )
                painter.setBrush(Qt.BrushStyle.NoBrush)
            # Playhead (float coords + antialiasing for smooth motion);
            # clamped so an out-of-range time keeps the head visible.
            px = min(max((self._time - self._lo) * px_per_sec, 0.0), float(w))
            painter.setPen(QPen(QColor(0xFF, 0x45, 0x3A), 2))
            painter.drawLine(QPointF(px, 0.0), QPointF(px, float(h)))
        painter.end()
