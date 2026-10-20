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

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from ..core.sync import ViewId
from .theme import VIEW_COLORS, on_view_color

SCRUB_PREVIEW_INTERVAL_S = 0.06  # min interval between scrub preview seeks

# Candidate tick spacings (seconds); the smallest one that keeps ticks
# MIN_TICK_PX apart is used, so long videos don't overlap or force
# thousands of draw calls per repaint.
TICK_STEPS_S = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800)
MIN_TICK_PX = 64.0

TRACK_LEFT = 26.0  # room for the per-track A/B/C/D badge
TRACK_RIGHT = 12.0
TRACK_TOP_Y = 13.0  # top of the first track, below the tick strip
TRACK_PITCH = 12.0  # vertical distance between track tops
TRACK_H = 6.0
BOTTOM_PAD = 13.0  # below the last track: knob overhang + breathing room
KNOB_R = 5.5

COLOR_CARD = QColor(0x16, 0x16, 0x18)
COLOR_BORDER = QColor(255, 255, 255, 18)
COLOR_DEAD = QColor(255, 255, 255, 13)  # beyond this video's duration
COLOR_KNOB = QColor(255, 255, 255)
COLOR_ANCHOR = QColor(0xFF, 0xD6, 0x0A)  # sync anchor, system yellow
COLOR_TICK = QColor(255, 255, 255, 30)

# Played fill per view, matching the badge colours (theme.py).
TRACK_FILL: dict[str, QColor] = {view: QColor(color) for view, color in VIEW_COLORS.items()}


def track_x_to_master(
    x: float,
    width: float,
    span: float,
    view: ViewId,
    offset: float,
    master: ViewId = "a",
) -> float:
    """Map a pixel on any track to master time (clamped to the track range).

    The tracks share one seconds scale: the reference track is the master
    axis itself, every follower track is shifted by its own sync offset.
    """
    if span <= 0 or width <= 0:
        return 0.0
    frac = min(1.0, max(0.0, x / width))
    t = frac * span
    return t if view == master else t - offset


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
    hovered_view = Signal(object)  # ViewId | None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Hover highlighting needs move events without a pressed button.
        self.setMouseTracking(True)
        self._init_state()
        self.setMinimumHeight(tracks_height(len(self._views)))

    def _init_state(self) -> None:
        """Initialize the render-state tables."""
        self._views: list[ViewId] = ["a", "b"]
        self._master: ViewId = "a"
        self._dur: dict[ViewId, float | None] = {}
        self._anchor: dict[ViewId, float | None] = {}
        self._offsets: dict[ViewId, float] = {}
        self._t: dict[ViewId, float] = {}
        self._drag_view: ViewId | None = None
        self._hover_view: ViewId | None = None
        self._last_preview = 0.0

    # -- state ----------------------------------------------------------------

    @property
    def _span(self) -> float:
        """Shared scale: the longest of the loaded durations."""
        return max((d for d in self._dur.values() if d), default=0.0)

    def _is_loaded(self, view: ViewId) -> bool:
        """A track is interactive only once its video reports a duration."""
        return self._dur.get(view) is not None

    def _offset(self, view: ViewId) -> float:
        """This track's sync offset for scrub mapping (0 when unsynced).

        Fed directly by the window layer from each engine's own offset, so
        the mapping can never disagree with the chip/status readouts.
        """
        if view == self._master:
            return 0.0
        return self._offsets.get(view, 0.0)

    def set_master(self, view: ViewId) -> None:
        """Set the reference track (the unshifted master axis)."""
        if view not in self._views:
            view = self._views[0] if self._views else "a"
        if view == self._master:
            return
        self._master = view
        self._offsets.pop(view, None)
        self.update()

    def set_tracks(self, views: Sequence[ViewId]) -> None:
        """Set the track list; state of surviving views is preserved."""
        self._views = list(views)
        for table in (self._dur, self._anchor, self._offsets, self._t):
            for view in list(table):
                if view not in self._views:
                    del table[view]
        if self._master not in self._views:
            self._master = self._views[0] if self._views else "a"
        if self._hover_view not in self._views:
            self._hover_view = None
        if self._drag_view not in self._views:
            self._drag_view = None
        for view in self._views:
            self._dur.setdefault(view, None)
            self._anchor.setdefault(view, None)
            self._t.setdefault(view, 0.0)
        if self._hover_view is not None and not self._is_loaded(self._hover_view):
            self._set_hover(None)
        self.setMinimumHeight(tracks_height(len(self._views)))
        self.update()

    def set_durations(self, durs: Mapping[ViewId, float | None]) -> None:
        """Set each track's full scale; None marks an unloaded view."""
        changed = False
        for view, dur in durs.items():
            if view not in self._views:
                continue
            normalized = dur if dur and dur > 0 else None
            if view not in self._dur or self._dur[view] != normalized:
                self._dur[view] = normalized
                changed = True
        if not changed:
            return
        if self._hover_view is not None and not self._is_loaded(self._hover_view):
            self._set_hover(None)
        if self._drag_view is not None and not self._is_loaded(self._drag_view):
            self._drag_view = None
        self.update()

    def set_anchors(self, anchors: Mapping[ViewId, float | None]) -> None:
        """Place the sync anchors (a None value clears that anchor)."""
        for view, anchor in anchors.items():
            if view not in self._views:
                continue
            self._anchor[view] = anchor
        self.update()

    def set_offsets(self, offsets: Mapping[ViewId, float]) -> None:
        """Set each follower's sync offset used by scrub mapping."""
        self._offsets = {
            view: off
            for view, off in offsets.items()
            if view != self._master and view in self._views
        }
        self.update()

    def set_playheads(self, times: Mapping[ViewId, float]) -> None:
        """Move the knobs (each in its own video's local time)."""
        for view, t in times.items():
            if view not in self._views:
                continue
            self._t[view] = t
        self.update()

    @property
    def hovered_view_id(self) -> ViewId | None:
        return self._hover_view

    def set_hovered_view(self, view: ViewId | None) -> None:
        """Highlight a loaded track (driven by pane hover); no signal feedback."""
        if view is not None and not self._is_loaded(view):
            self._set_hover(None)
            return
        if view == self._hover_view:
            return
        self._hover_view = view
        self.update()

    # -- input ----------------------------------------------------------------

    def _hover_at(self, y: float) -> ViewId | None:
        """Track under the pointer, or None over ticks/gaps/outside."""
        if not self._views:
            return None
        top = TRACK_TOP_Y - 4.0
        bottom = TRACK_TOP_Y + (len(self._views) - 1) * TRACK_PITCH + TRACK_H + 4.0
        if y < top or y > bottom:
            return None
        view = self._view_at(y)
        return view if self._is_loaded(view) else None

    def _set_hover(self, view: ViewId | None) -> None:
        if view is not None and not self._is_loaded(view):
            view = None
        if view == self._hover_view:
            return
        self._hover_view = view
        self.hovered_view.emit(view)
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        self._set_hover(None)
        super().leaveEvent(event)

    def _view_at(self, y: float) -> ViewId:
        """Nearest track by y; clamped to the first/last track."""
        idx = int((y - TRACK_TOP_Y - TRACK_H / 2) / TRACK_PITCH + 0.5)
        return self._views[min(max(idx, 0), len(self._views) - 1)]

    def _x_to_master(self, x: float, view: ViewId) -> float:
        usable = max(1.0, float(self.width()) - TRACK_LEFT - TRACK_RIGHT)
        return track_x_to_master(
            x - TRACK_LEFT,
            usable,
            self._span,
            view,
            self._offset(view),
            self._master,
        )

    def _emit_preview(self, t: float, *, force: bool = False) -> None:
        now = time.monotonic()
        if force or now - self._last_preview >= SCRUB_PREVIEW_INTERVAL_S:
            self._last_preview = now
            self.scrub_preview.emit(t)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start scrubbing on a loaded track; immediate preview."""
        if event.button() == Qt.MouseButton.LeftButton:
            view = self._view_at(event.position().y())
            if not self._is_loaded(view):
                return
            self._drag_view = view
            self._set_hover(view)
            self._emit_preview(self._x_to_master(event.position().x(), view), force=True)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Throttled coarse preview while scrubbing; hover highlight otherwise."""
        view = self._drag_view
        if view is not None:
            self._emit_preview(self._x_to_master(event.position().x(), view))
        else:
            self._set_hover(self._hover_at(event.position().y()))

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """Commit an exact seek at the release position."""
        view = self._drag_view
        if event.button() == Qt.MouseButton.LeftButton and view is not None:
            self._drag_view = None
            self.seek_committed.emit(self._x_to_master(event.position().x(), view))

    # -- painting ---------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the slider tracks as an inset macOS-style card."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            painter.end()
            return
        card = QRectF(0.5, 0.5, w - 1.0, h - 1.0)
        painter.setPen(QPen(COLOR_BORDER, 1.0))
        painter.setBrush(COLOR_CARD)
        painter.drawRoundedRect(card, 10.0, 10.0)

        usable = max(1.0, float(w) - TRACK_LEFT - TRACK_RIGHT)
        span = self._span
        if span > 0:
            px_per_sec = usable / span
            # Label-free second ticks along the top; adaptive step bounds
            # draw cost for arbitrarily long ranges.
            step = next(
                (s for s in TICK_STEPS_S if s * px_per_sec >= MIN_TICK_PX),
                TICK_STEPS_S[-1],
            )
            painter.setPen(QPen(COLOR_TICK, 1.0))
            t = 0.0
            while t <= span:
                x = TRACK_LEFT + t * px_per_sec
                painter.drawLine(QPointF(x, 3.0), QPointF(x, 8.0))
                t += step
            for i, view in enumerate(self._views):
                y = TRACK_TOP_Y + i * TRACK_PITCH
                hovered = view == self._hover_view and self._is_loaded(view)
                self._paint_track(painter, usable, view, y, px_per_sec, hovered)
        painter.end()

    def _paint_track(
        self,
        painter: QPainter,
        usable: float,
        view: ViewId,
        y: float,
        px_per_sec: float,
        hovered: bool,
    ) -> None:
        dur = self._dur.get(view)
        t_cur = self._t.get(view, 0.0)
        anchor = self._anchor.get(view)
        active_w = (dur or 0.0) * px_per_sec

        # Identity badge: keeps A-D recognisable without relying on colour.
        color = TRACK_FILL.get(view, TRACK_FILL["a"])
        badge = QRectF(9.0, y - 2.0, 10.0, 10.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(badge, 3.0, 3.0)
        if hovered:
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1.2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(badge.adjusted(-1.0, -1.0, 1.0, 1.0), 4.0, 4.0)
        badge_font = QFont(painter.font())
        badge_font.setPointSizeF(6.3)
        badge_font.setBold(True)
        painter.setFont(badge_font)
        painter.setPen(QColor(on_view_color(view)))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, view.upper())

        painter.setPen(Qt.PenStyle.NoPen)
        # Dead zone: the full-width base darker than the active track;
        # for an unloaded (or shorter) video it shows what's missing.
        painter.setBrush(COLOR_DEAD)
        painter.drawRoundedRect(QRectF(TRACK_LEFT, y, usable, TRACK_H), 3.0, 3.0)
        if active_w > 0.0:
            painter.setBrush(QColor(255, 255, 255, 70 if hovered else 34))
            painter.drawRoundedRect(QRectF(TRACK_LEFT, y, active_w, TRACK_H), 3.0, 3.0)
            # Played fill; a hair of minimum width keeps the rounded ends
            # from degenerating at tiny positions, but never past the
            # active track into the dead zone.
            fill_w = max(min(t_cur * px_per_sec, active_w), 0.0)
            if fill_w > 0.0:
                fill = QColor(color)
                fill.setAlpha(215)
                painter.setBrush(fill)
                painter.drawRoundedRect(
                    QRectF(
                        TRACK_LEFT,
                        y,
                        min(max(fill_w, 6.0), active_w),
                        TRACK_H,
                    ),
                    3.0,
                    3.0,
                )
        # Sync anchor: yellow stem through the track + diamond at centre.
        if anchor is not None and dur is not None:
            ax = TRACK_LEFT + anchor * px_per_sec
            if TRACK_LEFT <= ax <= TRACK_LEFT + usable:
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
            kx = TRACK_LEFT + min(max(t_cur * px_per_sec, 0.0), active_w)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 120))
            painter.drawEllipse(QPointF(kx, y + TRACK_H / 2 + 0.5), KNOB_R + 1.0, KNOB_R + 1.0)
            painter.setBrush(COLOR_KNOB)
            painter.drawEllipse(QPointF(kx, y + TRACK_H / 2), KNOB_R, KNOB_R)
