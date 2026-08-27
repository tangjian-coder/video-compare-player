"""Application bootstrap: libmpv path setup + Qt entry."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .config import VENDOR_DIR, AppConfig

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

logger = logging.getLogger(__name__)

ICON_SIZES = (256, 128, 64, 48, 32, 16)


def _apply_window_icon(app: QApplication) -> None:
    """Set the app icon from assets/ (multi-resolution PNG bundle)."""
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for size in ICON_SIZES:
        path = VENDOR_DIR.parent / "assets" / f"icon-{size}.png"
        if path.is_file():
            icon.addFile(str(path), QSize(size, size))
    if not icon.isNull():
        app.setWindowIcon(icon)
    else:
        logger.warning("no app icon files found under assets/")


def _prepare_libmpv_path() -> None:
    """Make vendor/libmpv-2.dll discoverable before mpv is imported."""
    if VENDOR_DIR.is_dir():
        os.environ["PATH"] = str(VENDOR_DIR) + os.pathsep + os.environ.get("PATH", "")
        os.add_dll_directory(str(VENDOR_DIR))
    else:
        logger.warning("vendor dir missing: %s (libmpv-2.dll required)", VENDOR_DIR)


def _prepare_timer_resolution() -> None:
    """Raise Windows timer resolution so the regulation loop ticks on time."""
    import atexit
    import ctypes

    with contextlib.suppress(Exception):
        ctypes.windll.winmm.timeBeginPeriod(1)  # type: ignore[attr-defined]
        atexit.register(ctypes.windll.winmm.timeEndPeriod, 1)  # type: ignore[attr-defined]


def run(left: Path | None, right: Path | None) -> int:
    """Create the Qt application and enter the event loop."""
    _prepare_libmpv_path()
    _prepare_timer_resolution()
    sys.setswitchinterval(0.001)  # the lockstep worker is latency-sensitive

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from .ui.main_window import MainWindow
    from .ui.style import apply_app_style

    app = QApplication(sys.argv)
    app.setApplicationName("video-compare-player")
    _apply_window_icon(app)
    apply_app_style(app)

    config = AppConfig.load()
    window = MainWindow(config)
    window.show()
    # Players need native windows, so initialize after show().
    QTimer.singleShot(0, lambda: window.initialize(left=left, right=right))

    logger.info("entering Qt event loop")
    return app.exec()
