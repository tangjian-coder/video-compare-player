"""Shared visual tokens for QPainter code and the QSS template."""

from __future__ import annotations

VIEW_COLORS: dict[str, str] = {
    "a": "#0a84ff",
    "b": "#30d158",
    "c": "#5e5ce6",
    "d": "#ff9f0a",
}
ACCENT_BLUE = "#0a84ff"
ON_LIGHT_TEXT = "#101012"
TEXT_ON_COLOR = "#ffffff"


def on_view_color(view_id: str) -> str:
    """Text colour with enough contrast on that view's badge colour."""
    return ON_LIGHT_TEXT if view_id.lower() in ("b", "d") else TEXT_ON_COLOR
