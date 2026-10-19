"""Video display block: titled frame + native mpv surface + per-view controls.

Layout mirrors Kinovea's dual-screen panes: each block has its own header
(badge + filename), the video surface, and a footer with per-view frame
stepping and sync controls.

Rendering is done by libmpv directly into the surface's native window.
Zoom/pan use mpv's GPU-side properties (video-zoom / video-pan-x/y).
"""

from __future__ import annotations

import logging

from PySide6.QtCore import (
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QContextMenuEvent,
    QCursor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QEnterEvent,
    QFont,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from ..core.player import PlayerController
from .icons import make_icon
from .layout import ZONE_CENTER, zone_at
from .pane_drag import decode_view_id, first_video_path, make_view_mime
from .theme import ACCENT_BLUE, VIEW_COLORS, on_view_color

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


def step_zoom(current: float, direction: int) -> float:
    """Snap to the next zoom stop in the given direction.

    Tolerates float round-trips through mpv's log2 zoom property.
    """
    idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - current))
    idx = max(0, min(len(ZOOM_STEPS) - 1, idx + direction))
    return ZOOM_STEPS[idx]


def pixels_to_pan_delta(dx: float, dy: float, disp_w: float, disp_h: float) -> tuple[float, float]:
    """Convert pointer pixel deltas to mpv pan fractions.

    mpv pans in fractions of the scaled video size, so the same pixel
    distance is a larger fraction on a smaller display. Zero-sized
    displays yield no movement.
    """
    if disp_w <= 0 or disp_h <= 0:
        return 0.0, 0.0
    return dx / disp_w, dy / disp_h


def rotated_size(w: float, h: float, rotation: int) -> tuple[float, float]:
    """Effective video dimensions after display rotation."""
    return (h, w) if rotation % 180 else (w, h)


class MpvSurface(QWidget):
    """Native render surface for libmpv; owns mouse input for zoom/pan."""

    clicked = Signal()
    file_dropped = Signal(str)
    zoom_changed = Signal(float)  # current linear zoom factor
    # Gestures relay to the paired surface unless Ctrl is held: the default
    # intent in a comparison tool is steering both views at once, Ctrl opts
    # this side out. Zoom steps relay as a direction, pan as pointer pixel
    # deltas (each side converts them with its own display size, keeping
    # pointer movement 1:1 everywhere).
    paired_zoom = Signal(int)
    paired_pan = Signal(float, float)
    paired_reset = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(400, 260)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors)
        # Never take keyboard focus: this is a native child window, and
        # once focused it swallows keystrokes before Qt can route them,
        # killing every QShortcut (Space / arrows / S / R / F9). Mouse
        # interaction is unaffected by focus policy.
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAcceptDrops(True)
        self._player: PlayerController | None = None
        self._drag_origin: tuple[float, float] | None = None
        self._pan_origin: tuple[float, float] = (0.0, 0.0)
        # Last move-event position: paired pan relays per-event deltas.
        self._last_move_pos: tuple[float, float] | None = None
        self._wheel_accum = 0  # high-res trackpads emit many small deltas

    @property
    def has_player(self) -> bool:
        """True once ensure_player() has created the mpv instance."""
        return self._player is not None

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
        """Displayed video size in pixels (aspect-fit * zoom); None unknown.

        Rotation-aware: a 90/270-degree turn swaps the effective
        dimensions before the aspect-fit, matching how mpv presents the
        rotated frame. Pan limits and pixel->pan conversion both depend
        on this.
        """
        if self._player is None or self._player.meta is None:
            return None
        meta = self._player.meta
        vw, vh = rotated_size(float(meta.width), float(meta.height), self._player.rotation)
        w, h = float(self.width()), float(self.height())
        if vw <= 0 or vh <= 0 or w <= 0 or h <= 0:
            return None
        scale = min(w / vw, h / vh) * self._player.zoom
        return vw * scale, vh * scale

    def _pan_limits(self) -> tuple[float, float] | None:
        """Per-axis max |pan| that keeps a video edge at the window edge."""
        disp = self._display_size()
        if disp is None:
            return None
        disp_w, disp_h = disp
        w, h = float(self.width()), float(self.height())
        return (
            max(0.0, (disp_w - w) / (2.0 * disp_w)),
            max(0.0, (disp_h - h) / (2.0 * disp_h)),
        )

    def _clamp_pan(self, x: float, y: float) -> tuple[float, float]:
        """Clamp pan so the video can never leave the window fully out of view."""
        limits = self._pan_limits()
        if limits is None:
            return x, y
        mx, my = limits
        return min(max(x, -mx), mx), min(max(y, -my), my)

    def _step_zoom(self, direction: int) -> None:
        """Advance this surface's zoom by one stop; re-clamp pan."""
        assert self._player is not None
        z0 = self._player.zoom
        z1 = step_zoom(z0, direction)
        if z1 == z0:
            return
        px0, py0 = self._player.pan
        self._player.set_zoom(z1)
        # Re-clamp: the allowed pan range shrinks when zooming out.
        self._player.set_pan(*self._clamp_pan(px0, py0))
        self.zoom_changed.emit(z1)

    def wheelEvent(self, event: QWheelEvent) -> None:
        """Zoom by discrete stops; Ctrl limits the step to this view."""
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
        self._step_zoom(direction)
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.paired_zoom.emit(direction)
        event.accept()

    def apply_paired_zoom(self, direction: int) -> None:
        """Handle a paired (no-Ctrl) wheel step relayed from another surface."""
        if self._player is not None:
            self._step_zoom(direction)

    def apply_paired_pan(self, dx_px: float, dy_px: float) -> None:
        """Handle a paired (no-Ctrl) drag pan relayed from another surface.

        Converts the pointer's pixel delta with this surface's own
        display size, so both views track the mouse 1:1 even at
        different resolutions or zoom levels.
        """
        if self._player is None:
            return
        disp = self._display_size()
        if disp is None:
            return
        x0, y0 = self._player.pan
        fdx, fdy = pixels_to_pan_delta(dx_px, dy_px, disp[0], disp[1])
        x, y = self._clamp_pan(x0 + fdx, y0 + fdy)
        self._player.set_pan(x, y)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Record drag origin and mark this view as the active one."""
        self.clicked.emit()
        if event.button() == Qt.MouseButton.LeftButton and self._player is not None:
            self._drag_origin = (event.position().x(), event.position().y())
            self._pan_origin = self._player.pan
            self._last_move_pos = self._drag_origin
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Pan the video 1:1 with the pointer while dragging.

        Without Ctrl the per-event pixel delta is relayed to the paired
        surface (which converts it with its own display size, keeping both
        views tracking the mouse). Ctrl keeps the pan on this view.
        """
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
        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            last = self._last_move_pos or self._drag_origin
            self.paired_pan.emit(event.position().x() - last[0], event.position().y() - last[1])
        self._last_move_pos = (event.position().x(), event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """End a pan drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = None
            self._last_move_pos = None
            self.unsetCursor()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Reset zoom, pan and rotation; Ctrl limits the reset to this side."""
        if self._player is not None:
            self._player.reset_view()
            self.zoom_changed.emit(1.0)
            if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                self.paired_reset.emit()

    def apply_paired_reset(self) -> None:
        """Handle a double-click reset relayed from the other surface."""
        if self._player is not None:
            self._player.reset_view()
            self.zoom_changed.emit(1.0)

    # -- drag & drop ----------------------------------------------------------

    def _owning_view(self) -> VideoView | None:
        """The VideoView this native surface belongs to (for pane drags)."""
        parent = self.parentWidget()
        while parent is not None:
            if isinstance(parent, VideoView):
                return parent
            parent = parent.parentWidget()
        return None

    def _owning_pane_drag(self, event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> str | None:
        """Source view id when this is an internal pane drag, else None."""
        return decode_view_id(event.mimeData())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept pane drags on the video area too, plus video files."""
        source = self._owning_pane_drag(event)
        if source is not None:
            view = self._owning_view()
            if view is not None:
                if source != view.view_name.lower():
                    view.pane_drag_move(self.mapTo(view, event.position().toPoint()), source)
                event.acceptProposedAction()
                return
        if first_video_path(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Track the pane drop zone while the pointer is over the video."""
        source = self._owning_pane_drag(event)
        view = self._owning_view()
        if source is None or view is None:
            return
        if source != view.view_name.lower():
            view.pane_drag_move(self.mapTo(view, event.position().toPoint()), source)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        view = self._owning_view()
        if view is not None:
            view.set_drop_zone(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """Forward pane drops on the video area; keep file drops local."""
        source = self._owning_pane_drag(event)
        view = self._owning_view()
        if source is not None and view is not None:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            view.pane_drag_drop(source, self.mapTo(view, event.position().toPoint()))
            return
        local = first_video_path(event.mimeData())
        if local is not None:
            self.file_dropped.emit(local)
            event.acceptProposedAction()


class _ElidedLabel(QLabel):
    """Label that keeps the full text but elides it to the available width.

    Narrow panes (2x2, 1x4) would otherwise demand the full file name and
    inflate the pane's minimum width.
    """

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        super().setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 - Qt override
        self._full_text = text
        self.setToolTip(text)
        self._refresh()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        width = max(0, self.width())
        elided = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, width)
        super().setText(elided)


class _PlaceholderPane(QWidget):
    """Empty-state pane that never dictates the pane's minimum height.

    Its icon and copy would otherwise raise the whole grid's height floor
    four times over in stacked layouts.
    """

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(0, 0)


class _HeaderBar(QWidget):
    """Pane header: drag source for arranging, plus the context menu.

    Dragging starts here (not on the video surface) so the existing
    wheel zoom / drag pan gestures stay untouched. QDrag takes an OS-level
    mouse grab, which also keeps the drag from leaking into those handlers.
    """

    drag_started = Signal(str)  # source view id
    drag_finished = Signal()
    clicked = Signal()
    context_menu_requested = Signal(object)  # QPoint

    def __init__(self, view_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view_name = view_name
        self._title = f"View {view_name}"
        self._press_pos: QPoint | None = None
        self._drag_enabled = True
        self.setToolTip(f"Drag to rearrange view {view_name} · right-click for layout presets")
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def set_title(self, title: str) -> None:
        """Text shown on the drag chip (loaded file name or default)."""
        self._title = title

    def _idle_cursor(self) -> Qt.CursorShape:
        return Qt.CursorShape.OpenHandCursor if self._drag_enabled else Qt.CursorShape.ArrowCursor

    def set_drag_enabled(self, enabled: bool) -> None:
        """Allow or block starting a pane drag (blocked while recording)."""
        self._drag_enabled = enabled
        if enabled:
            self.setToolTip(
                f"Drag to rearrange view {self._view_name} · right-click for layout presets"
            )
        else:
            self.setToolTip(f"View {self._view_name} — stop recording to rearrange")
        self.setCursor(self._idle_cursor())

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_pos = event.position().toPoint()
            if self._drag_enabled:
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press_pos is None:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            self._press_pos = None
            self.setCursor(self._idle_cursor())
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._start_drag()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press_pos is not None:
            self.clicked.emit()
        self._press_pos = None
        self.setCursor(self._idle_cursor())
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:  # noqa: N802 - Qt override
        self.context_menu_requested.emit(event.globalPos())

    def _drag_pixmap(self) -> QPixmap:
        """Compact dark chip with the coloured badge and the file name.

        Grabbing the widget itself produced a transparent, full-width strip
        that showed as a plain white bar under the cursor on Windows.
        """
        width, height = 180, 44
        dpr = self.devicePixelRatioF()
        pixmap = QPixmap(int(width * dpr), int(height * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        body = QRectF(0.5, 1.5, width - 1.0, height - 6.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(body.translated(0.0, 1.5), 9.0, 9.0)
        painter.setBrush(QColor(44, 44, 46, 230))
        painter.setPen(QPen(QColor("#5c5c60"), 1.0))
        painter.drawRoundedRect(body, 9.0, 9.0)

        letter = self._view_name.upper()[:1]
        badge = QRectF(8.0, 8.0, 24.0, 24.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(VIEW_COLORS.get(letter.lower(), ACCENT_BLUE)))
        painter.drawRoundedRect(badge, 6.0, 6.0)

        badge_font = QFont(self.font())
        badge_font.setBold(True)
        badge_font.setPointSizeF(9.5)
        painter.setFont(badge_font)
        painter.setPen(QColor(on_view_color(letter)))
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, letter)

        title_font = QFont(self.font())
        title_font.setPointSizeF(8.5)
        painter.setFont(title_font)
        painter.setPen(QColor("#f2f2f7"))
        metrics = painter.fontMetrics()
        available = width - 38.0 - 18.0
        title = metrics.elidedText(self._title, Qt.TextElideMode.ElideMiddle, int(available))
        painter.drawText(
            QRectF(38.0, 8.0, available, 24.0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            title,
        )

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#98989d"))
        for row in range(3):
            for col in range(2):
                painter.drawEllipse(QPointF(width - 13.0 + col * 4.5, 14.0 + row * 5.5), 1.2, 1.2)
        painter.end()
        return pixmap

    def _start_drag(self) -> None:
        self._press_pos = None
        if not self._drag_enabled:
            return
        drag = QDrag(self)
        drag.setMimeData(make_view_mime(self._view_name))
        drag.setPixmap(self._drag_pixmap())
        drag.setHotSpot(QPoint(10, 10))
        self.drag_started.emit(self._view_name.lower())
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self.setCursor(self._idle_cursor())
            self.drag_finished.emit()


class VideoView(QFrame):
    """One comparison block: header (badge + filename), video, footer controls."""

    clicked = Signal(str)  # emits view_name
    file_dropped = Signal(str)
    layout_drag_started = Signal(str)  # source view id
    layout_drag_finished = Signal()
    view_dropped_on = Signal(str, str, str)  # source, target, drop zone
    context_menu_requested = Signal(str, object)  # view id, QPoint
    drop_zone_changed = Signal(str, str)  # target view id, zone ("" = cleared)
    hover_changed = Signal(str, bool)  # view id, pointer inside/outside

    def __init__(self, view_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view_name = view_name
        self.setObjectName("videoView")
        self.setAcceptDrops(True)  # catches drops while the placeholder is showing
        self._drag_source_active = False
        self._playing_icon_state: bool | None = None
        self._hovered = False
        self.setProperty("trackHover", "")
        self.setProperty("activePane", "")

        self._badge = QLabel(view_name)
        # Distinct object names give A/B badges their system-blue/green fills.
        self._badge.setObjectName(f"viewBadge{view_name}")
        self._badge.setFixedSize(22, 22)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._caption = _ElidedLabel("no video")
        self._caption.setObjectName("captionLabel")
        self._header = _HeaderBar(view_name, self)
        header = QHBoxLayout(self._header)
        header.setContentsMargins(10, 6, 10, 0)
        header.setSpacing(6)
        header.addWidget(self._badge)
        header.addWidget(self._caption, stretch=1)
        self._grip = QLabel("⠿")
        self._grip.setObjectName("headerGrip")
        self._grip.setToolTip("Drag the header to rearrange this pane")
        header.addWidget(self._grip)
        self._drop_zone_name: str | None = None

        self._placeholder = _PlaceholderPane()
        placeholder_layout = QVBoxLayout(self._placeholder)
        placeholder_layout.setContentsMargins(24, 24, 24, 24)
        placeholder_layout.setSpacing(6)
        placeholder_layout.addStretch(1)
        placeholder_icon = QLabel()
        placeholder_icon.setPixmap(make_icon("video", "#5c6166").pixmap(28, 28))
        placeholder_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.addWidget(placeholder_icon)
        placeholder_title = QLabel(f"Drop video {view_name}")
        placeholder_title.setObjectName("placeholderTitle")
        placeholder_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.addWidget(placeholder_title)
        placeholder_hint = QLabel("or use the Open menu")
        placeholder_hint.setObjectName("placeholderHint")
        placeholder_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.addWidget(placeholder_hint)
        placeholder_drag = QLabel("Drag the header to move this pane")
        placeholder_drag.setObjectName("placeholderHint")
        placeholder_drag.setAlignment(Qt.AlignmentFlag.AlignCenter)
        placeholder_layout.addWidget(placeholder_drag)
        placeholder_layout.addStretch(1)
        self.surface = MpvSurface(self)
        self.surface.installEventFilter(self)
        self._stack = QStackedLayout()
        self._stack.addWidget(self._placeholder)
        self._stack.addWidget(self.surface)

        self.btn_step_back = QPushButton()
        self.btn_step_back.setObjectName("miniButton")
        self.btn_step_back.setIcon(make_icon("step-back"))
        self.btn_step_back.setIconSize(QSize(14, 14))
        self.btn_step_back.setToolTip(f"Step {view_name} back one frame")
        self.btn_step_fwd = QPushButton()
        self.btn_step_fwd.setObjectName("miniButton")
        self.btn_step_fwd.setIcon(make_icon("step-fwd"))
        self.btn_step_fwd.setIconSize(QSize(14, 14))
        self.btn_step_fwd.setToolTip(f"Step {view_name} forward one frame")
        self.btn_play = QPushButton()
        self.btn_play.setObjectName("miniButton")
        self.btn_play.setIcon(make_icon("play"))
        self.btn_play.setIconSize(QSize(14, 14))
        self.btn_play.setToolTip(f"Play/pause {view_name} alone (for finding the sync frame)")
        self.btn_rotate = QPushButton()
        self.btn_rotate.setObjectName("miniButton")
        self.btn_rotate.setIcon(make_icon("rotate"))
        self.btn_rotate.setIconSize(QSize(14, 14))
        self.btn_rotate.setToolTip(
            f"Rotate {view_name} 90° clockwise (angle shown beside the button;"
            " double-click the video to reset)"
        )
        # Dedicated angle readout: a 26px icon button cannot fit "180°"
        # without clipping. Fixed width reserves the space at 0° so the
        # footer never shifts (which can leave stale pixels next to the
        # native video surface).
        self._rotation_label = QLabel("")
        self._rotation_label.setObjectName("rotationLabel")
        self._rotation_label.setFixedWidth(30)
        # NoFocus like the transport buttons: a focused mini button would
        # re-trigger on Space (double-toggle play / step-off the anchor).
        for btn in (self.btn_step_back, self.btn_step_fwd, self.btn_play, self.btn_rotate):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # Default text matches the always-rendered design (see
        # _on_zoom_changed): empty here would leave the footer without a
        # readout until the first wheel event.
        self._zoom_label = QLabel("×1.0")
        self._zoom_label.setObjectName("zoomLabel")
        self._zoom_label.setToolTip("Current zoom factor (double-click the video to reset)")
        self._time_label = QLabel("--")
        self._time_label.setObjectName("timeLabel")
        footer = QHBoxLayout()
        footer.setContentsMargins(8, 2, 8, 8)
        footer.setSpacing(4)
        footer.addStretch(1)
        footer.addWidget(self.btn_step_back)
        footer.addWidget(self.btn_step_fwd)
        footer.addWidget(self.btn_play)
        footer.addWidget(self.btn_rotate)
        footer.addWidget(self._rotation_label)
        footer.addStretch(1)
        footer.addWidget(self._zoom_label)
        footer.addSpacing(12)  # keep the zoom readout clear of the time text
        footer.addWidget(self._time_label)
        footer.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self._header)
        layout.addLayout(self._stack, stretch=1)
        layout.addLayout(footer)

        self._header.drag_started.connect(self._on_header_drag_started)
        self._header.drag_finished.connect(self._on_header_drag_finished)
        self._header.clicked.connect(lambda: self.clicked.emit(self.view_name))
        self._header.context_menu_requested.connect(
            lambda pos: self.context_menu_requested.emit(view_name.lower(), pos)
        )
        self.surface.clicked.connect(lambda: self.clicked.emit(self.view_name))
        self.surface.file_dropped.connect(self.file_dropped)
        # The signal carries the just-written zoom value: display that
        # directly instead of re-reading the mpv property (a round-trip
        # that can serve a stale value inside the slot call chain).
        self.surface.zoom_changed.connect(self._on_zoom_changed)
        self.btn_rotate.clicked.connect(self._on_rotate)

    def _cursor_inside(self) -> bool:
        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    def _set_hovered(self, hovered: bool) -> None:
        if hovered == self._hovered:
            return
        self._hovered = hovered
        self.hover_changed.emit(self.view_name.lower(), hovered)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt override
        self._set_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        self._set_hovered(self._cursor_inside())
        super().leaveEvent(event)

    def eventFilter(  # noqa: N802 - Qt override
        self, watched: QObject, event: QEvent
    ) -> bool:
        """Catch hover on the native mpv surface (it never forwards to us)."""
        if watched is self.surface:
            if event.type() == QEvent.Type.Enter:
                self._set_hovered(True)
            elif event.type() == QEvent.Type.Leave:
                self._set_hovered(self._cursor_inside())
        return super().eventFilter(watched, event)

    def _on_header_drag_started(self, source: str) -> None:
        self.set_drag_source(True)
        self.layout_drag_started.emit(source)

    def _on_header_drag_finished(self) -> None:
        self.set_drag_source(False)
        self.layout_drag_finished.emit()

    def set_drag_source(self, active: bool) -> None:
        """Mark this pane as the one being dragged (dashed border)."""
        if active == self._drag_source_active:
            return
        self._drag_source_active = active
        self.setProperty("dragSource", "true" if active else "")
        self.style().unpolish(self)
        self.style().polish(self)

    def _on_zoom_changed(self, zoom: float) -> None:
        """Show the zoom factor.

        Always rendered, including x1.0: every stop formats to the same
        4 characters, so the label width never changes. A collapsing
        (hidden-at-unity) label shifts the footer layout and, with the
        embedded mpv native child present, the vacated pixels are not
        reliably repainted - leaving stale text on screen.
        """
        self._zoom_label.setText(f"×{zoom:.1f}")
        # Resets clear rotation too; wheel steps never change it but the
        # extra read is a cheap local attribute, not an mpv round-trip.
        self._set_rotate_text()

    def _on_rotate(self) -> None:
        """Rotate this view 90° clockwise (pan recenters, zoom survives)."""
        if self.surface.has_player:
            self.surface.player.rotate_cw()
        self._set_rotate_text()

    def _set_rotate_text(self) -> None:
        """Show the current angle beside the icon (local state only)."""
        player = self.surface.player if self.surface.has_player else None
        rotation = player.rotation if player is not None else 0
        self._rotation_label.setText(f"{rotation}°" if rotation else "")
        self.btn_rotate.setToolTip(
            f"Rotate this view 90° clockwise (current: {rotation}°;"
            " double-click the video to reset)"
        )

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
        self._header.set_title(name)
        self._stack.setCurrentWidget(self.surface)

    def set_time_text(self, text: str) -> None:
        """Update the per-view time readout."""
        self._time_label.setText(text)

    def set_play_text(self, playing: bool) -> None:
        """Update the per-view play/pause button icon and tooltip."""
        if self._playing_icon_state == playing:
            return
        self._playing_icon_state = playing
        self.btn_play.setIcon(make_icon("pause" if playing else "play"))
        self.btn_play.setToolTip("Pause this view" if playing else "Play this view alone")

    # -- drag & drop on the container (placeholder state) -----------------------

    def set_drag_enabled(self, enabled: bool) -> None:
        """Enable/disable dragging this pane by its header."""
        self._header.set_drag_enabled(enabled)

    def set_drop_zone(self, zone: str | None) -> None:
        """Highlight the pending drop zone (or clear it)."""
        if zone == self._drop_zone_name:
            return
        self._drop_zone_name = zone
        self.setProperty("dropZone", zone or "")
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()
        # The host draws the insertion line in the gap between panes.
        notify = getattr(self.parentWidget(), "set_drop_indicator", None)
        if callable(notify):
            notify(self, zone)
        self.drop_zone_changed.emit(self.view_name.lower(), zone or "")

    def _drop_zone(self, pos: QPoint) -> str:
        # Dropping on another pane's header is the most natural gesture:
        # swap, rather than treating the header as the top edge zone.
        if self._header.geometry().contains(pos):
            return ZONE_CENTER
        return zone_at(float(pos.x()), float(pos.y()), float(self.width()), float(self.height()))

    @staticmethod
    def _drag_source(event: QDragEnterEvent | QDragMoveEvent | QDropEvent) -> str | None:
        """Decode the source view id from an internal pane drag."""
        return decode_view_id(event.mimeData())

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept pane drags and video-file drops."""
        source = self._drag_source(event)
        if source is not None:
            if source != self.view_name.lower():
                event.acceptProposedAction()
            return
        if first_video_path(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Track the drop zone under the pointer for pane drags."""
        source = self._drag_source(event)
        if source is None:
            return
        self.pane_drag_move(event.position().toPoint(), source)
        event.acceptProposedAction()

    def pane_drag_move(self, pos: QPoint, source: str) -> None:
        """Highlight the drop zone for an internal pane drag."""
        if source == self.view_name.lower():
            self.set_drop_zone(None)
            return
        self.set_drop_zone(self._drop_zone(pos))

    def pane_drag_drop(self, source: str, pos: QPoint) -> None:
        """Commit an internal pane drop at the given position."""
        zone = self._drop_zone(pos)
        self.set_drop_zone(None)
        if source != self.view_name.lower():
            self.view_dropped_on.emit(source, self.view_name.lower(), zone)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.set_drop_zone(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        """Apply a pane drop locally or forward a dropped video path."""
        source = self._drag_source(event)
        if source is not None:
            event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
            self.pane_drag_drop(source, event.position().toPoint())
            return
        local = first_video_path(event.mimeData())
        if local is not None:
            self.file_dropped.emit(local)
            event.acceptProposedAction()
