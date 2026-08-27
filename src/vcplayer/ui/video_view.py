"""Video display block: titled frame + native mpv surface + per-view controls.

Layout mirrors Kinovea's dual-screen panes: each block has its own header
(badge + filename), the video surface, and a footer with per-view frame
stepping and sync controls.

Rendering is done by libmpv directly into the surface's native window.
Zoom/pan use mpv's GPU-side properties (video-zoom / video-pan-x/y).
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..core.player import PlayerController

logger = logging.getLogger(__name__)

# Discrete zoom stops: fine steps at low magnification, coarser up high.
ZOOM_STEPS = [
    1.0,
    1.1,
    1.2,
    1.3,
    1.4,
    1.5,
    1.6,
    1.7,
    1.8,
    1.9,
    2.0,  # +0.1
    2.2,
    2.4,
    2.6,
    2.8,
    3.0,
    3.2,
    3.4,
    3.6,
    3.8,
    4.0,  # +0.2
    4.5,
    5.0,
    5.5,
    6.0,
    6.5,
    7.0,
    7.5,
    8.0,  # +0.5
]
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mts", ".m4v"}


def step_zoom(current: float, direction: int) -> float:
    """Snap to the next zoom stop in the given direction.

    Tolerates float round-trips through mpv's log2 zoom property.
    """
    idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - current))
    idx = max(0, min(len(ZOOM_STEPS) - 1, idx + direction))
    return ZOOM_STEPS[idx]


class MpvSurface(QWidget):
    """Native render surface for libmpv; owns mouse input for zoom/pan."""

    clicked = Signal()
    file_dropped = Signal(str)
    zoom_changed = Signal(float)  # current linear zoom factor

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 260)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        self.setAcceptDrops(True)
        self._player: PlayerController | None = None
        self._drag_origin: tuple[float, float] | None = None
        self._pan_origin: tuple[float, float] = (0.0, 0.0)
        self._wheel_accum = 0  # high-res trackpads emit many small deltas

    @property
    def player(self) -> PlayerController:
        """The embedded player; raises if ensure_player() was never called."""
        if self._player is None:
            raise RuntimeError("player not created; call ensure_player() first")
        return self._player

    def ensure_player(self) -> PlayerController:
        """Create the player on first use; winId() forces native handle creation."""
        if self._player is None:
            self._player = PlayerController(int(self.winId()))
            logger.info("surface player created, wid=%s", int(self.winId()))
        return self._player

    # -- zoom / pan -----------------------------------------------------------

    # mpv pans in fractions of the *scaled video* size (not the window), so:
    # - a 1:1 pointer grab divides mouse pixels by the displayed video size;
    # - pan survives zoom changes unchanged (mpv re-anchors about the window
    #   center itself; no manual compensation needed).

    def _display_size(self) -> tuple[float, float] | None:
        """Displayed video size in pixels (aspect-fit * zoom); None unknown."""
        if self._player is None or self._player.meta is None:
            return None
        meta = self._player.meta
        w, h = float(self.width()), float(self.height())
        if meta.width <= 0 or meta.height <= 0 or w <= 0 or h <= 0:
            return None
        scale = min(w / meta.width, h / meta.height) * self._player.zoom
        return meta.width * scale, meta.height * scale

    def _pan_limits(self) -> tuple[float, float] | None:
        """Per-axis max |pan| that keeps a video edge at the window edge."""
        disp = self._display_size()
        if disp is None:
            return None
        disp_w, disp_h = disp
        w, h = float(self.width()), float(self.height())
        return (
            abs(disp_w - w) / (2.0 * disp_w),
            abs(disp_h - h) / (2.0 * disp_h),
        )

    def _clamp_pan(self, x: float, y: float) -> tuple[float, float]:
        """Clamp pan so the video can never leave the window fully out of view."""
        limits = self._pan_limits()
        if limits is None:
            return x, y
        mx, my = limits
        return min(max(x, -mx), mx), min(max(y, -my), my)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom by discrete stops, anchored on the window center."""
        if self._player is None:
            return
        # Accumulate deltas so high-resolution trackpads advance one stop
        # per ~120 units instead of one stop per event.
        self._wheel_accum += event.angleDelta().y()
        direction = 0
        while self._wheel_accum >= 120:
            direction += 1
            self._wheel_accum -= 120
        while self._wheel_accum <= -120:
            direction -= 1
            self._wheel_accum += 120
        if direction == 0:
            return
        z0 = self._player.zoom
        z1 = step_zoom(z0, direction)
        if z1 == z0:
            return
        px0, py0 = self._player.pan
        self._player.set_zoom(z1)
        # Re-clamp: the allowed pan range shrinks when zooming out.
        self._player.set_pan(*self._clamp_pan(px0, py0))
        self.zoom_changed.emit(z1)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Record drag origin and mark this view as the active one."""
        self.clicked.emit()
        if event.button() == Qt.MouseButton.LeftButton and self._player is not None:
            self._drag_origin = (event.position().x(), event.position().y())
            self._pan_origin = self._player.pan
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Pan the video 1:1 with the pointer while dragging."""
        if self._drag_origin is None or self._player is None:
            return
        disp = self._display_size()
        if disp is None:
            return
        disp_w, disp_h = disp
        dx = (event.position().x() - self._drag_origin[0]) / disp_w
        dy = (event.position().y() - self._drag_origin[1]) / disp_h
        raw_x, raw_y = self._pan_origin[0] + dx, self._pan_origin[1] + dy
        x, y = self._clamp_pan(raw_x, raw_y)
        self._player.set_pan(x, y)
        if x != raw_x or y != raw_y:
            # Clamped at the edge: rebase so the drag resumes from here
            # instead of having to unwind the blocked excess first.
            self._pan_origin = (x, y)
            self._drag_origin = (event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End a pan drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Reset zoom and pan."""
        if self._player is not None:
            self._player.reset_view()
            self.zoom_changed.emit(1.0)

    # -- drag & drop ----------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept drops of video files."""
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if Path(url.toLocalFile()).suffix.lower() in VIDEO_EXTS:
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:
        """Emit the dropped video path."""
        mime = event.mimeData()
        if mime is None:
            return
        for url in mime.urls():
            local = url.toLocalFile()
            if Path(local).suffix.lower() in VIDEO_EXTS:
                self.file_dropped.emit(local)
                event.acceptProposedAction()
                return


class VideoView(QFrame):
    """One comparison block: header (badge + filename), video, footer controls."""

    clicked = Signal(str)  # emits view_name
    file_dropped = Signal(str)

    def __init__(self, view_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view_name = view_name
        self.setObjectName("videoView")
        self.setAcceptDrops(True)  # catches drops while the placeholder is showing

        self._badge = QLabel(view_name)
        # Distinct object names give A/B badges their system-blue/green fills.
        self._badge.setObjectName(f"viewBadge{view_name}")
        self._caption = QLabel("no video")
        self._caption.setObjectName("captionLabel")
        header = QHBoxLayout()
        header.setContentsMargins(8, 6, 8, 0)
        header.addWidget(self._badge)
        header.addWidget(self._caption, stretch=1)

        self._placeholder = QLabel(f"Drop video {view_name} here\nor click Open {view_name}")
        self._placeholder.setObjectName("placeholderLabel")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.surface = MpvSurface(self)
        self._stack = QStackedLayout()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self.surface)

        self.btn_step_back = QPushButton("<")
        self.btn_step_back.setObjectName("miniButton")
        self.btn_step_back.setToolTip(f"Step {view_name} back one frame")
        self.btn_step_fwd = QPushButton(">")
        self.btn_step_fwd.setObjectName("miniButton")
        self.btn_step_fwd.setToolTip(f"Step {view_name} forward one frame")
        self.btn_play = QPushButton("Play")
        self.btn_play.setObjectName("miniButton")
        self.btn_play.setToolTip(f"Play/pause {view_name} alone (for finding the sync frame)")
        self._zoom_label = QLabel("")
        self._zoom_label.setObjectName("zoomLabel")
        self._zoom_label.setToolTip("Current zoom factor (double-click the video to reset)")
        self._time_label = QLabel("--")
        self._time_label.setObjectName("timeLabel")
        footer = QHBoxLayout()
        footer.setContentsMargins(8, 0, 8, 6)
        footer.addStretch(1)
        footer.addWidget(self.btn_step_back)
        footer.addWidget(self.btn_step_fwd)
        footer.addWidget(self.btn_play)
        footer.addStretch(1)
        footer.addWidget(self._zoom_label)
        footer.addWidget(self._time_label)
        footer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addLayout(self._stack, stretch=1)
        layout.addLayout(footer)

        self.surface.clicked.connect(lambda: self.clicked.emit(self.view_name))
        self.surface.file_dropped.connect(self.file_dropped)
        self.surface.zoom_changed.connect(self._on_zoom_changed)

    @property
    def player(self) -> PlayerController:
        """The embedded player; raises if ensure_player() was never called."""
        return self.surface.player

    def ensure_player(self) -> PlayerController:
        """Create the underlying mpv player (idempotent)."""
        return self.surface.ensure_player()

    def set_loaded_name(self, name: str) -> None:
        """Show the loaded file name and switch from placeholder to video."""
        self._caption.setText(name)
        self._stack.setCurrentWidget(self.surface)

    def set_time_text(self, text: str) -> None:
        """Update the per-view time readout."""
        self._time_label.setText(text)

    def set_play_text(self, playing: bool) -> None:
        """Update the per-view play button label."""
        self.btn_play.setText("Pause" if playing else "Play")

    def _on_zoom_changed(self, zoom: float) -> None:
        """Show the zoom factor; hide the label when at unity."""
        if abs(zoom - 1.0) < 0.005:
            self._zoom_label.clear()
        else:
            self._zoom_label.setText(f"×{zoom:.1f}")

    # -- drag & drop on the container (placeholder state) -----------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept video drops that land on the placeholder area."""
        mime = event.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if Path(url.toLocalFile()).suffix.lower() in VIDEO_EXTS:
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event: QDropEvent) -> None:
        """Forward a dropped video path that landed on the placeholder area."""
        mime = event.mimeData()
        if mime is None:
            return
        for url in mime.urls():
            local = url.toLocalFile()
            if Path(local).suffix.lower() in VIDEO_EXTS:
                self.file_dropped.emit(local)
                event.acceptProposedAction()
                return
