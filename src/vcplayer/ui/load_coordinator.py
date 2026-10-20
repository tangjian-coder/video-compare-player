"""Background polling for asynchronous video metadata loads."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from ..core.player import PlayerController
from ..core.sync import ViewId

logger = logging.getLogger(__name__)

LOAD_TICK_MS = 100
LOAD_TIMEOUT_MS = 10_000


@dataclass
class _PendingLoad:
    path: Path
    player: PlayerController
    waited_ms: int = 0


class LoadCoordinator(QObject):
    """Owns the pending-load set and polls each player for metadata.

    The coordinator never opens dialogs: it emits ``loaded`` or ``failed``
    after removing the entry, so a modal event loop cannot re-enter the scan.
    """

    loaded = Signal(str, object, object)  # view id, Path, VideoMeta
    failed = Signal(str, object, str)  # view id, Path, message
    progress = Signal()  # one pending poll completed

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        tick_ms: int = LOAD_TICK_MS,
        timeout_ms: int = LOAD_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent)
        self._tick_ms = tick_ms
        self._timeout_ms = timeout_ms
        self._pending: dict[ViewId, _PendingLoad] = {}
        self._timer = QTimer(self)
        self._timer.setInterval(tick_ms)
        self._timer.timeout.connect(self.poll)

    @property
    def pending_ids(self) -> set[ViewId]:
        return set(self._pending)

    @property
    def pending_paths(self) -> dict[ViewId, Path]:
        return {vid: item.path for vid, item in self._pending.items()}

    def is_loading(self, view: ViewId) -> bool:
        return view in self._pending

    def start(self, view: ViewId, path: Path, player: PlayerController) -> None:
        """Begin polling a load that the player has already accepted."""
        self._pending[view] = _PendingLoad(path=path, player=player)
        if not self._timer.isActive():
            self._timer.start()

    def cancel(self, view: ViewId) -> None:
        """Forget a pending load; the caller is responsible for the player."""
        self._pending.pop(view, None)
        self._stop_if_idle()

    def clear(self) -> None:
        """Forget every pending load without touching the players."""
        self._pending.clear()
        self._timer.stop()

    def poll(self) -> None:
        """Advance each pending load once; emit results outside the scan."""
        if not self._pending:
            self._timer.stop()
            return
        results: list[tuple[str, ViewId, Path, object, str | None]] = []
        for view, item in list(self._pending.items()):
            meta = item.player.try_capture_meta()
            if meta is None and not item.player.is_failed and item.waited_ms < self._timeout_ms:
                item.waited_ms += self._tick_ms
                continue
            del self._pending[view]
            if meta is not None:
                results.append(("loaded", view, item.path, meta, None))
                continue
            if item.player.is_failed:
                message = item.player.load_error or f"Failed to load:\n{item.path}"
            elif item.player.duration is not None:
                message = (
                    f"No frame rate in this container:\n{item.path}\n"
                    "(sync comparison needs a readable frame rate)"
                )
            else:
                message = f"Timed out loading:\n{item.path}"
            item.player.mark_load_failed(message)
            results.append(("failed", view, item.path, None, message))
        self._stop_if_idle()
        self.progress.emit()
        for kind, view, path, result_meta, result_message in results:
            if kind == "loaded":
                self.loaded.emit(view, path, result_meta)
            else:
                logger.warning("load failed for %s: %s", view, result_message)
                self.failed.emit(view, path, result_message or "load failed")

    def _stop_if_idle(self) -> None:
        if not self._pending:
            self._timer.stop()
