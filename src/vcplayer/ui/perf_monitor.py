"""Qt heartbeat that reports event-loop stalls when performance logging is on."""

from __future__ import annotations

import logging

from PySide6.QtCore import QElapsedTimer, QObject, QTimer

from .. import perf

logger = logging.getLogger(__name__)

HEARTBEAT_MS = 16
STALL_WARN_MS = 50


class UiStallMonitor(QObject):
    """Logs when the GUI thread misses its heartbeat deadline."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._elapsed = QElapsedTimer()
        self._timer = QTimer(self)
        self._timer.setInterval(HEARTBEAT_MS)
        self._timer.timeout.connect(self._tick)
        self._max_gap_ms = 0

    def start(self) -> None:
        if not perf.enabled() or self._timer.isActive():
            return
        self._elapsed.start()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._max_gap_ms:
            perf.mark("ui.max_stall", ms=self._max_gap_ms)
            self._max_gap_ms = 0

    def _tick(self) -> None:
        gap = self._elapsed.restart()
        self._max_gap_ms = max(self._max_gap_ms, gap)
        if gap >= STALL_WARN_MS:
            perf.mark("ui.stall", ms=gap)
