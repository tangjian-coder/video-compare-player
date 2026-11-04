"""Detect loaded source files disappearing from disk."""

from __future__ import annotations

import logging
import stat
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer, Signal

logger = logging.getLogger(__name__)

POLL_INTERVAL_MS = 1_000
CONFIRM_DELAY_MS = 250
CONFIRM_MISSES = 2

FileIdentity = tuple[int, int]


class SourceMonitor(QObject):
    """Watch loaded paths and emit once when a source file disappears."""

    missing = Signal(str, object)  # view id, Path

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        poll_ms: int = POLL_INTERVAL_MS,
        confirm_ms: int = CONFIRM_DELAY_MS,
        confirm_misses: int = CONFIRM_MISSES,
    ) -> None:
        super().__init__(parent)
        self._paths: dict[str, Path] = {}
        self._identities: dict[str, FileIdentity | None] = {}
        self._missing: set[str] = set()
        self._misses: dict[str, int] = {}
        self._confirm_misses = max(1, confirm_misses)
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._schedule_check)
        self._watcher.directoryChanged.connect(self._schedule_check)
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(confirm_ms)
        self._check_timer.timeout.connect(self.check_now)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(poll_ms)
        self._poll_timer.timeout.connect(self.check_now)

    def track(self, view: str, path: Path) -> None:
        """Start watching one loaded view's source path."""
        source = Path(path)
        self._paths[view] = source
        self._identities[view] = self._identity(source)
        self._missing.discard(view)
        self._misses[view] = 0
        self._sync_watch_paths()
        if not self._poll_timer.isActive():
            self._poll_timer.start()

    def forget(self, view: str) -> None:
        """Stop watching a view that was unloaded or removed."""
        self._paths.pop(view, None)
        self._identities.pop(view, None)
        self._missing.discard(view)
        self._misses.pop(view, None)
        self._sync_watch_paths()

    def clear(self) -> None:
        """Stop watching every view."""
        self._paths.clear()
        self._identities.clear()
        self._missing.clear()
        self._misses.clear()
        self._check_timer.stop()
        self._poll_timer.stop()
        self._sync_watch_paths()

    def path_for(self, view: str) -> Path | None:
        return self._paths.get(view)

    def is_missing(self, view: str) -> bool:
        return view in self._missing

    def check_now(self) -> None:
        """Verify all tracked paths; public for tests and watcher callbacks."""
        for view, path in list(self._paths.items()):
            if view in self._missing:
                continue
            identity, exists = self._inspect(path)
            previous = self._identities.get(view)
            if exists and identity is not None and previous is not None and identity != previous:
                self._mark_missing(view, path, "was replaced")
                continue
            if exists:
                if identity is not None:
                    self._identities[view] = identity
                self._misses[view] = 0
                continue
            misses = self._misses.get(view, 0) + 1
            self._misses[view] = misses
            if misses < self._confirm_misses:
                continue
            self._mark_missing(view, path, "disappeared")

    def _mark_missing(self, view: str, path: Path, reason: str) -> None:
        self._missing.add(view)
        logger.warning("source file %s: %s <- %s", reason, view, path)
        self.missing.emit(view, path)

    @staticmethod
    def _identity(path: Path) -> FileIdentity | None:
        identity, _exists = SourceMonitor._inspect(path)
        return identity

    @staticmethod
    def _inspect(path: Path) -> tuple[FileIdentity | None, bool]:
        try:
            info = path.stat()
        except OSError:
            return None, False
        if not stat.S_ISREG(info.st_mode):
            return None, False
        if not info.st_ino:
            return None, True
        return (info.st_dev, info.st_ino), True

    def _schedule_check(self, _path: str = "") -> None:
        if self._paths and not self._check_timer.isActive():
            self._check_timer.start()

    def _sync_watch_paths(self) -> None:
        watched = set(self._watcher.files()) | set(self._watcher.directories())
        desired: set[str] = set()
        for path in self._paths.values():
            desired.add(str(path))
            desired.add(str(path.parent))
        stale = watched - desired
        if stale:
            self._watcher.removePaths(sorted(stale))
        missing = desired - watched
        if missing:
            failed = self._watcher.addPaths(sorted(missing))
            if failed:
                logger.debug("could not watch paths: %s", failed)
        if not self._paths:
            self._poll_timer.stop()
