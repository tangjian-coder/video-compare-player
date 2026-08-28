"""Dual-track timeline: one slider per video on a shared time scale.

Both tracks share the same seconds-to-pixels scale (span = the longer
video), so the two knobs move at identical speed during playback and the
two sync anchors sit horizontally apart by exactly the sync offset.
Each track spans its own video's full duration; the tail beyond it is
rendered as a dead zone.
"""

from __future__ import annotations

import time
from typing import Literal

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

SCRUB_PREVIEW_INTERVAL_S = 0.06  # min interval between scrub preview seeks

# Candidate tick spacings (seconds); the smallest one that keeps ticks
# MIN_TICK_PX apart is used, so long videos don't overlap or force
# thousands of draw calls per repaint.
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800)
MIN_TICK_PX = 64.0

WIDGET_H = 44  # tick strip + two tracks, constant in every state
TRACK_H = 6.0
TRACK_A_Y = 13.0  # top of the A track
TRACK_B_Y = 25.0  # top of the B track
TRACK_MID_Y = (TRACK_A_Y + TRACK_B_Y + TRACK_H) / 2  # hit-test boundary
KNOB_R = 5.5

COLOR_BG = QColor(0x1C, 0x1C, 0x1E)
COLOR_TRACK = QColor(0x48, 0x48, 0x4A)  # active track (video has frames)
COLOR_DEAD = QColor(0x2C, 0x2C, 0x2E)  # beyond this video's duration
COLOR_FILL = QColor(0x0A, 0x84, 0xFF)  # played portion, system blue
COLOR_KNOB = QColor(0xFF, 0xFF, 0xFF)
COLOR_ANCHOR = QColor(0xFF, 0xD6, 0x0A)  # sync anchor, system yellow
COLOR_TICK = QColor(0x48, 0x48, 0x4A)

ViewId = Literal["a", "b"]


def track_x_to_master(x: float, width: float, span: float, view: ViewId, offset: float) -> float:
    """Map a pixel on either track to master time (unclamped).

    The tracks share one seconds scale: the A track is the master axis
    itself, the B track is shifted by the sync offset.
    """
    if span <= 0 or width <= 0:
        return 0.0
    frac = min(1.0, max(0.0, x / width))
    t = frac * span
    return t if view == "a" else t - offset


class TimelineWidget(QWidget):
    """Two-slider ruler; click/drag either slider to seek both views.

    Scrubbing emits throttled `scrub_preview` signals (coarse seeks) and
    a single `seek_committed` on release (exact seek), so dragging stays
    fluid even with high-fps, high-bitrate sources.
    """

    scrub_preview = Signal(float)
    seek_committed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(WIDGET_H)
        self._dur_a: float | None = None
        self._dur_b: float | None = None
        self._anchor_a: float | None = None
        self._anchor_b: float | None = None
        self._t_a = 0.0
        self._t_b = 0.0
        self._drag_view: ViewId | None = None
        self._last_preview = 0.0

    # -- state ----------------------------------------------------------------

    @property
    def _span(self) -> float:
        """Shared scale: the longer of the two durations."""
        return max(self._dur_a or 0.0, self._dur_b or 0.0)

    @property
    def _offset(self) -> float:
        """Sync offset derived from the anchors (0 when unsynced)."""
        if self._anchor_a is None or self._anchor_b is None:
            return 0.0
        return self._anchor_b - self._anchor_a

    def set_durations(self, dur_a: float | None, dur_b: float | None) -> None:
        """Set each track's full scale; None marks an unloaded view."""
        self._dur_a = dur_a if dur_a and dur_a > 0 else None
        self._dur_b = dur_b if dur_b and dur_b > 0 else None
        self.update()

    def set_anchors(self, a: float | None, b: float | None) -> None:
        """Place the sync anchors (both None = unsynced)."""
        self._anchor_a = a
        self._anchor_b = b
        self.update()

    def set_playheads(self, t_a: float, t_b: float) -> None:
        """Move both knobs (each in its own video's local time)."""
        self._t_a = t_a
        self._t_b = t_b
        self.update()

    # -- input ----------------------------------------------------------------

    def _view_at(self, y: float) -> ViewId:
        return "a" if y < TRACK_MID_Y else "b"

    def _x_to_master(self, x: float, view: ViewId) -> float:
        return track_x_to_master(x, float(self.width()), self._span, view, self._offset)

    def _emit_preview(self, t: float, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_preview >= SCRUB_PREVIEW_INTERVAL_S:
            self._last_preview = now
            self.scrub_preview.emit(t)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start scrubbing on the pressed track; immediate preview."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_view = self._view_at(event.position().y())
            self._emit_preview(self._x_to_master(event.position().x(), self._drag_view), force=True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Throttled coarse preview while scrubbing."""
        view = self._drag_view
        if view is not None:
            self._emit_preview(self._x_to_master(event.position().x(), view))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Commit an exact seek at the release position."""
        view = self._drag_view
        if event.button() == Qt.MouseButton.LeftButton and view is not None:
            self._drag_view = None
            self.seek_committed.emit(self._x_to_master(event.position().x(), view))

    # -- painting ---------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the two slider tracks (macOS dark palette).

        Layering per track: dead zone (full width), active track (this
        video's duration), played fill (blue), sync anchor (yellow stem
        + diamond), knob (white, drawn last so it rides over everything).
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(0, 0, w, h, COLOR_BG)
        span = self._span
        if span > 0 and w > 0:
            px_per_sec = w / span
            # Label-free second ticks along the top; adaptive step bounds
            # draw cost for arbitrarily long ranges.
            step = next(
                (s for s in TICK_STEPS_S if s * px_per_sec >= MIN_TICK_PX),
                TICK_STEPS_S[-1],
            )
            painter.setPen(QPen(COLOR_TICK))
            t = 0.0
            while t <= span:
                x = t * px_per_sec
                painter.drawLine(QPointF(x, 2.0), QPointF(x, 9.0))
                t += step
            for view in ("a", "b"):
                self._paint_track(painter, w, view, px_per_sec)
        painter.end()

    def _paint_track(self, painter: QPainter, w: int, view: ViewId, px_per_sec: float) -> None:
        dur = self._dur_a if view == "a" else self._dur_b
        t_cur = self._t_a if view == "a" else self._t_b
        anchor = self._anchor_a if view == "a" else self._anchor_b
        y = TRACK_A_Y if view == "a" else TRACK_B_Y
        active_w = (dur or 0.0) * px_per_sec

        painter.setPen(Qt.PenStyle.NoPen)
        # Dead zone: the full-width base darker than the active track;
        # for an unloaded (or shorter) video it shows what's missing.
        painter.setBrush(COLOR_DEAD)
        painter.drawRoundedRect(QRectF(0.0, y, float(w), TRACK_H), 3.0, 3.0)
        if active_w > 0.0:
            painter.setBrush(COLOR_TRACK)
            painter.drawRoundedRect(QRectF(0.0, y, active_w, TRACK_H), 3.0, 3.0)
            # Played fill; a hair of minimum width keeps the rounded ends
            # from degenerating at tiny positions, but never past the
            # active track into the dead zone.
            fill_w = max(min(t_cur * px_per_sec, active_w), 0.0)
            if fill_w > 0.0:
                painter.setBrush(COLOR_FILL)
                painter.drawRoundedRect(
                    QRectF(0.0, y, min(max(fill_w, 6.0), active_w), TRACK_H), 3.0, 3.0
                )
        # Sync anchor: yellow stem through the track + diamond at centre.
        if anchor is not None and dur is not None:
            ax = anchor * px_per_sec
            if 0.0 <= ax <= w:
                cy = y + TRACK_H / 2
                painter.setPen(QPen(COLOR_ANCHOR, 2))
                painter.setBrush(COLOR_ANCHOR)
                painter.drawLine(QPointF(ax, cy - 7.0), QPointF(ax, cy + 7.0))
                painter.drawPolygon(
                    [
                        QPointF(ax, cy - 5.0),
                        QPointF(ax + 4.0, cy),
                        QPointF(ax, cy + 5.0),
                        QPointF(ax - 4.0, cy),
                    ]
                )
                painter.setBrush(Qt.BrushStyle.NoBrush)
        # Knob last so it rides over fill, ticks and anchor stem.
        if dur is not None:
            kx = min(max(t_cur * px_per_sec, 0.0), active_w)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(COLOR_KNOB)
            painter.drawEllipse(QPointF(kx, y + TRACK_H / 2), KNOB_R, KNOB_R)
