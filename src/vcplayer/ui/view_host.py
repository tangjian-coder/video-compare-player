"""Stable grid container for the comparison panes.

Views are never reparented here: MpvSurface renders into a native child
window and a reparent would recreate the HWND (black video). The host only
moves widgets within its own QGridLayout, which is a pure geometry change.
"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtWidgets import QGridLayout, QWidget

from .layout import (
    GRID_COLUMNS,
    ZONE_BOTTOM,
    ZONE_CENTER,
    ZONE_LEFT,
    ZONE_RIGHT,
    ZONE_TOP,
    Layout,
    cells,
)
from .video_view import VIEW_MIME, VideoView


class ViewHost(QWidget):
    """Grid host owning the pane widgets; layout changes never reparent."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(6)
        self._grid.setVerticalSpacing(6)
        for col in range(GRID_COLUMNS):
            self._grid.setColumnStretch(col, 1)
        self._views: dict[str, VideoView] = {}
        self._rows = 0
        # Pane drags: the host accepts them everywhere (including the gaps
        # between panes) so the OS never shows a "forbidden" cursor inside
        # the pane area, and forwards the gesture to the pane under the
        # pointer (or the nearest one when in a gap).
        self.setAcceptDrops(True)
        self._drop_pane: VideoView | None = None
        self._indicator: tuple[VideoView, str] | None = None

    @property
    def views(self) -> dict[str, VideoView]:
        """Views currently hosted, keyed by lowercase view id."""
        return dict(self._views)

    @property
    def count(self) -> int:
        """Number of hosted views."""
        return len(self._views)

    def add_view(self, view: VideoView) -> None:
        """Register a pane; it is placed by the next apply_layout()."""
        self._views[view.view_name.lower()] = view

    def remove_view(self, view: VideoView) -> None:
        """Detach a doomed pane from the grid (only during teardown)."""
        vid = view.view_name.lower()
        if self._views.get(vid) is view:
            del self._views[vid]
        if self._drop_pane is view:
            self._drop_pane = None
        if self._indicator is not None and self._indicator[0] is view:
            self._indicator = None
        self._grid.removeWidget(view)
        view.setParent(None)

    def apply_layout(self, layout: Layout) -> None:
        """Place every hosted pane according to the row-list layout."""
        for view in self._views.values():
            self._grid.removeWidget(view)
        while self._rows:
            self._rows -= 1
            self._grid.setRowStretch(self._rows, 0)
        for vid, row, col, span in cells(layout):
            pane = self._views.get(vid)
            if pane is not None:
                self._grid.addWidget(pane, row, col, 1, span)
        self._rows = len(layout)
        for row in range(self._rows):
            self._grid.setRowStretch(row, 1)

    # -- pane drag proxy --------------------------------------------------------

    @staticmethod
    def _mime_source(
        event: QDragEnterEvent | QDragMoveEvent | QDropEvent,
    ) -> str | None:
        mime = event.mimeData()
        if mime is None or not mime.hasFormat(VIEW_MIME):
            return None
        return bytes(mime.data(VIEW_MIME).data()).decode("ascii", errors="ignore") or None

    def _pane_at(self, pos: QPoint) -> VideoView | None:
        """Pane under the pointer; nearest pane when the pointer is in a gap."""
        placed = [view for view in self._views.values() if self._grid.indexOf(view) >= 0]
        for view in placed:
            if view.geometry().contains(pos):
                return view
        best: VideoView | None = None
        best_dist = float("inf")
        for view in placed:
            center = view.geometry().center()
            dist = float((center.x() - pos.x()) ** 2 + (center.y() - pos.y()) ** 2)
            if dist < best_dist:
                best = view
                best_dist = dist
        return best

    def set_drop_indicator(self, view: VideoView, zone: str | None) -> None:
        """Show/hide the insertion line a pane's drop zone asks the host to draw."""
        if self._views.get(view.view_name.lower()) is not view:
            return
        indicator = None if zone in (None, ZONE_CENTER) else (view, zone)
        if indicator == self._indicator:
            return
        self._indicator = indicator
        self.update()

    def clear_drop_state(self) -> None:
        if self._drop_pane is not None:
            self._drop_pane.set_drop_zone(None)
            self._drop_pane = None
        self._indicator = None
        self.update()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt override
        if self._mime_source(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # noqa: N802 - Qt override
        source = self._mime_source(event)
        if source is None:
            return
        pos = event.position().toPoint()
        pane = self._pane_at(pos)
        if pane is None:
            self.clear_drop_state()
            return
        if pane is not self._drop_pane:
            if self._drop_pane is not None:
                self._drop_pane.set_drop_zone(None)
            self._drop_pane = pane
        pane.pane_drag_move(pane.mapFrom(self, pos), source)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:  # noqa: N802 - Qt override
        self.clear_drop_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt override
        source = self._mime_source(event)
        if source is None:
            event.ignore()
            return
        pos = event.position().toPoint()
        pane = self._pane_at(pos)
        event.setDropAction(Qt.DropAction.MoveAction)
        event.accept()
        if pane is not None:
            pane.pane_drag_drop(source, pane.mapFrom(self, pos))
        self.clear_drop_state()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        if self._indicator is None:
            return
        view, zone = self._indicator
        geom = view.geometry()
        if zone == ZONE_LEFT:
            if geom.left() - 5 < 0:
                return  # outer edge: no gap, the pane border highlights instead
            rect = QRectF(geom.left() - 5, geom.top() + 2, 4, geom.height() - 4)
        elif zone == ZONE_RIGHT:
            if geom.right() + 6 > self.width():
                return
            rect = QRectF(geom.right() + 2, geom.top() + 2, 4, geom.height() - 4)
        elif zone == ZONE_TOP:
            if geom.top() - 5 < 0:
                return
            # A new row always spans the full grid width, not just the target.
            rect = QRectF(2, geom.top() - 5, self.width() - 4, 4)
        elif zone == ZONE_BOTTOM:
            if geom.bottom() + 6 > self.height():
                return
            rect = QRectF(2, geom.bottom() + 2, self.width() - 4, 4)
        else:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#0a84ff"))
        painter.drawRoundedRect(rect, 2.0, 2.0)
