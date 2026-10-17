"""Multi-track timeline: one slider per video on a shared time scale.

All tracks share the same seconds-to-pixels scale (span = the longest
video), so the knobs move at identical speed during playback and each
track's sync anchor sits at the same physical moment. Each track spans
its own video's full duration; the tail beyond it renders as a dead
zone. The track list is dynamic (2-4 views) and the height follows it.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ..core.sync import ViewId

SCRUB_PREVIEW_INTERVAL_S = 0.06  # min interval between scrub preview seeks

# Candidate tick spacings (seconds); the smallest one that keeps ticks
# MIN_TICK_PX apart is used, so long videos don't overlap or force
# thousands of draw calls per repaint.
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800)
MIN_TICK_PX = 64.0

TRACK_TOP_Y = 13.0  # top of the first track, below the tick strip
TRACK_PITCH = 12.0  # vertical distance between track tops
TRACK_H = 6.0
BOTTOM_PAD = 13.0  # below the last track: knob overhang + breathing room
KNOB_R = 5.5

COLOR_BG = QColor(0x1C, 0x1C, 0x1E)
COLOR_TRACK = QColor(0x48, 0x48, 0x4A)  # active track (video has frames)
COLOR_DEAD = QColor(0x2C, 0x2C, 0x2E)  # beyond this video's duration
COLOR_KNOB = QColor(0xFF, 0xFF, 0xFF)
COLOR_ANCHOR = QColor(0xFF, 0xD6, 0x0A)  # sync anchor, system yellow
COLOR_TICK = QColor(0x48, 0x48, 0x4A)

# Played fill per view, matching the badge colors (style.py).
TRACK_FILL: dict[ViewId, QColor] = {
    "a": QColor(0x0A, 0x84, 0xFF),
    "b": QColor(0x30, 0xD1, 0x58),
    "c": QColor(0x5E, 0x5C, 0xE6),
    "d": QColor(0xFF, 0x9F, 0x0A),
}


def track_x_to_master(x: float, width: float, span: float, view: ViewId, offset: float) -> float:
    """Map a pixel on any track to master time (unclamped).

    The tracks share one seconds scale: the A track is the master axis
    itself, every follower track is shifted by its own sync offset.
    """
    if span <= 0 or width <= 0:
        return 0.0
    frac = min(1.0, max(0.0, x / width))
    t = frac * span
    return t if view == "a" else t - offset


def tracks_height(count: int) -> int:
    """Widget height for `count` tracks (tick strip + tracks + padding)."""
    return round(TRACK_TOP_Y + (count - 1) * TRACK_PITCH + TRACK_H + BOTTOM_PAD)


class TimelineWidget(QWidget):
    """Multi-slider ruler; click/drag any slider to seek all views.

    Scrubbing emits throttled `scrub_preview` signals (coarse seeks) and
    a single `seek_committed` on release (exact seek), so dragging stays
    fluid even with high-fps, high-bitrate sources.
    """

    scrub_preview = Signal(float)
    seek_committed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_state()
        self.setMinimumHeight(tracks_height(len(self._views)))

    def _init_state(self) -> None:
        """Plain-Python state, split out for the no-Qt test fixture."""
        self._views: list[ViewId] = ["a", "b"]
        self._dur: dict[ViewId, float | None] = {}
        self._anchor: dict[ViewId, float | None] = {}
        self._offsets: dict[ViewId, float] = {}
        self._t: dict[ViewId, float] = {}
        self._drag_view: ViewId | None = None
        self._last_preview = 0.0

    # -- state ----------------------------------------------------------------

    @property
    def _span(self) -> float:
        """Shared scale: the longest of the loaded durations."""
        return max((d for d in self._dur.values() if d), default=0.0)

    def _offset(self, view: ViewId) -> float:
        """This track's sync offset for scrub mapping (0 when unsynced).

        Fed directly by the window layer from each engine's own offset, so
        the mapping can never disagree with the chip/status readouts.
        """
        if view == "a":
            return 0.0
        return self._offsets.get(view, 0.0)

    def set_tracks(self, views: Sequence[ViewId]) -> None:
        """Set the track list; state of surviving views is preserved."""
        self._views = list(views)
        for view in list(self._dur):
            if view not in self._views:
                del self._dur[view]
                self._anchor.pop(view, None)
                self._offsets.pop(view, None)
                self._t.pop(view, None)
        for view in self._views:
            self._dur.setdefault(view, None)
            self._anchor.setdefault(view, None)
            self._t.setdefault(view, 0.0)
        self.setMinimumHeight(tracks_height(len(self._views)))
        self.update()

    def set_durations(self, durs: Mapping[ViewId, float | None]) -> None:
        """Set each track's full scale; None marks an unloaded view."""
        for view, dur in durs.items():
            self._dur[view] = dur if dur and dur > 0 else None
        self.update()

    def set_anchors(self, anchors: Mapping[ViewId, float | None]) -> None:
        """Place the sync anchors (a None value clears that anchor)."""
        for view, anchor in anchors.items():
            self._anchor[view] = anchor
        self.update()

    def set_offsets(self, offsets: Mapping[ViewId, float]) -> None:
        """Set each follower's sync offset used by scrub mapping."""
        self._offsets = {view: off for view, off in offsets.items() if view != "a"}
        self.update()

    def set_playheads(self, times: Mapping[ViewId, float]) -> None:
        """Move the knobs (each in its own video's local time)."""
        for view, t in times.items():
            self._t[view] = t
        self.update()

    # -- input ----------------------------------------------------------------

    def _view_at(self, y: float) -> ViewId:
        """Nearest track by y; clamped to the first/last track."""
        idx = int((y - TRACK_TOP_Y - TRACK_H / 2) / TRACK_PITCH + 0.5)
        return self._views[min(max(idx, 0), len(self._views) - 1)]

    def _x_to_master(self, x: float, view: ViewId) -> float:
        return track_x_to_master(x, float(self.width()), self._span, view, self._offset(view))

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
        """Paint the slider tracks (macOS dark palette).

        Layering per track: dead zone (full width), active track (this
        video's duration), played fill (view color), sync anchor (yellow
        stem + diamond), knob (white, drawn last so it rides over all).
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
            for i, view in enumerate(self._views):
                y = TRACK_TOP_Y + i * TRACK_PITCH
                self._paint_track(painter, w, view, y, px_per_sec)
        painter.end()

    def _paint_track(
        self, painter: QPainter, w: int, view: ViewId, y: float, px_per_sec: float
    ) -> None:
        dur = self._dur.get(view)
        t_cur = self._t.get(view, 0.0)
        anchor = self._anchor.get(view)
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
                painter.setBrush(TRACK_FILL.get(view, TRACK_FILL["a"]))
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
