"""Small themed widgets that QSS cannot fully express."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QComboBox


class ChevronComboBox(QComboBox):
    """QComboBox with a drawn macOS-style chevron (QSS hides the native one)."""

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt override
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#98989d"), 1.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        x = float(self.width()) - 13.0
        y = float(self.height()) / 2.0
        painter.drawPolyline(
            [QPointF(x - 4.0, y - 2.0), QPointF(x, y + 2.0), QPointF(x + 4.0, y - 2.0)]
        )
        painter.end()
