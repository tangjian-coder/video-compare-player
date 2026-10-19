"""Monochrome UI icons drawn with QPainter (SF-Symbols-like line style).

Keeping the icons as code avoids shipping a third-party icon font and lets
every glyph share the same stroke weight, cap style and colour as the theme.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

DEFAULT_COLOR = "#e5e5ea"
CANVAS = 20.0
ICON_SIZES = (16, 20, 24, 32, 48, 64)


def _painter(pixmap: QPixmap, scale: float, color: str) -> QPainter:
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)
    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    return painter


def _draw(painter: QPainter, name: str, color: str) -> None:
    pen = painter.pen()
    if name == "play":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon([QPointF(7.0, 4.5), QPointF(16.0, 10.0), QPointF(7.0, 15.5)])
    elif name == "pause":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(6.0, 4.5, 3.0, 11.0), 1.2, 1.2)
        painter.drawRoundedRect(QRectF(11.0, 4.5, 3.0, 11.0), 1.2, 1.2)
    elif name == "step-back":
        painter.drawPolyline([QPointF(13.0, 5.0), QPointF(7.0, 10.0), QPointF(13.0, 15.0)])
    elif name == "step-fwd":
        painter.drawPolyline([QPointF(7.0, 5.0), QPointF(13.0, 10.0), QPointF(7.0, 15.0)])
    elif name == "skip-start":
        painter.drawLine(QPointF(5.0, 5.0), QPointF(5.0, 15.0))
        painter.drawPolyline([QPointF(14.0, 5.0), QPointF(8.0, 10.0), QPointF(14.0, 15.0)])
    elif name == "skip-end":
        painter.drawLine(QPointF(15.0, 5.0), QPointF(15.0, 15.0))
        painter.drawPolyline([QPointF(6.0, 5.0), QPointF(12.0, 10.0), QPointF(6.0, 15.0)])
    elif name == "record":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(QPointF(10.0, 10.0), 5.0, 5.0)
    elif name == "crosshair":
        painter.drawEllipse(QRectF(4.5, 4.5, 11.0, 11.0))
        painter.drawLine(QPointF(10.0, 2.0), QPointF(10.0, 6.0))
        painter.drawLine(QPointF(10.0, 14.0), QPointF(10.0, 18.0))
        painter.drawLine(QPointF(2.0, 10.0), QPointF(6.0, 10.0))
        painter.drawLine(QPointF(14.0, 10.0), QPointF(18.0, 10.0))
    elif name == "rotate":
        painter.drawArc(QRectF(4.0, 4.0, 12.0, 12.0), 40 * 16, 280 * 16)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon([QPointF(15.0, 5.0), QPointF(18.4, 8.2), QPointF(14.2, 10.4)])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
    elif name == "folder":
        painter.drawPolyline(
            [
                QPointF(8.0, 7.0),
                QPointF(9.5, 5.0),
                QPointF(16.0, 5.0),
                QPointF(16.0, 16.0),
                QPointF(3.0, 16.0),
                QPointF(3.0, 7.0),
                QPointF(8.0, 7.0),
            ]
        )
    elif name == "video":
        painter.drawRoundedRect(QRectF(2.5, 5.0, 15.0, 10.0), 2.0, 2.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawPolygon([QPointF(8.5, 7.8), QPointF(13.0, 10.0), QPointF(8.5, 12.2)])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(pen)
    elif name == "plus":
        painter.drawLine(QPointF(10.0, 5.0), QPointF(10.0, 15.0))
        painter.drawLine(QPointF(5.0, 10.0), QPointF(15.0, 10.0))
    elif name == "minus":
        painter.drawLine(QPointF(5.0, 10.0), QPointF(15.0, 10.0))
    elif name == "layout":
        for x in (3.5, 11.0):
            for y in (3.5, 11.0):
                painter.drawRoundedRect(QRectF(x, y, 5.5, 5.5), 1.5, 1.5)
    elif name == "chevron-down":
        painter.drawPolyline([QPointF(6.0, 8.0), QPointF(10.0, 12.0), QPointF(14.0, 8.0)])
    elif name == "x":
        painter.drawLine(QPointF(6.0, 6.0), QPointF(14.0, 14.0))
        painter.drawLine(QPointF(14.0, 6.0), QPointF(6.0, 14.0))
    elif name == "swap":
        painter.drawLine(QPointF(4.0, 7.5), QPointF(15.0, 7.5))
        painter.drawPolyline([QPointF(13.0, 5.0), QPointF(16.0, 7.5), QPointF(13.0, 10.0)])
        painter.drawLine(QPointF(16.0, 12.5), QPointF(5.0, 12.5))
        painter.drawPolyline([QPointF(7.0, 10.0), QPointF(4.0, 12.5), QPointF(7.0, 15.0)])
    painter.setPen(pen)


def make_icon(name: str, color: str = DEFAULT_COLOR) -> QIcon:
    """Build a multi-resolution QIcon for `name` in the given colour."""
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = _painter(pixmap, size / CANVAS, color)
        _draw(painter, name, color)
        painter.end()
        icon.addPixmap(pixmap)
    return icon
