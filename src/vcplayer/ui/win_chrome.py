"""Windows chrome helpers: force the native title bar into dark mode."""

from __future__ import annotations

import contextlib
import ctypes
import sys

from PySide6.QtWidgets import QWidget

# DWMWA_USE_IMMERSIVE_DARK_MODE is 20 on current Windows 10/11 builds
# (19 on older 1809-1903 builds).
_DWMWA_USE_IMMERSIVE_DARK_MODE = (20, 19)
_DWMWA_BORDER_COLOR = 34


def apply_dark_title_bar(widget: QWidget) -> None:
    """Paint the native caption in dark mode; no-op off Windows."""
    if sys.platform != "win32":
        return
    with contextlib.suppress(Exception):
        hwnd = int(widget.winId())
        value = ctypes.c_int(1)
        for attr in _DWMWA_USE_IMMERSIVE_DARK_MODE:
            hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            )
            if hr == 0:
                break
        # Blend the window border into the app background.
        border = ctypes.c_int(0x001C1C1E)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(  # type: ignore[attr-defined]
            hwnd, _DWMWA_BORDER_COLOR, ctypes.byref(border), ctypes.sizeof(border)
        )
