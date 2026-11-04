"""Tests for loaded-source disappearance detection."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from vcplayer.ui.source_monitor import SourceMonitor


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_missing_file_emits_once_after_confirmation(qapp: QApplication, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    monitor = SourceMonitor(poll_ms=60_000, confirm_misses=2)
    seen: list[tuple[str, Path]] = []
    monitor.missing.connect(lambda view, path: seen.append((view, path)))
    monitor.track("a", source)

    monitor.check_now()
    assert seen == []
    source.unlink()
    monitor.check_now()
    assert seen == []
    monitor.check_now()
    monitor.check_now()
    assert seen == [("a", source)]
    monitor.clear()


def test_replaced_file_emits_missing(qapp: QApplication, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"old")
    monitor = SourceMonitor(poll_ms=60_000, confirm_misses=1)
    seen: list[tuple[str, Path]] = []
    monitor.missing.connect(lambda view, path: seen.append((view, path)))
    monitor.track("a", source)
    old_id = (source.stat().st_dev, source.stat().st_ino)
    replacement = tmp_path / "replacement.mp4"
    replacement.write_bytes(b"new")
    replacement.replace(source)
    new_id = (source.stat().st_dev, source.stat().st_ino)
    if old_id == new_id:
        monitor.clear()
        pytest.skip("filesystem reused the same file id")
    monitor.check_now()
    assert seen == [("a", source)]
    monitor.clear()


def test_existing_file_never_reports_missing(qapp: QApplication, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    monitor = SourceMonitor(poll_ms=60_000, confirm_misses=1)
    seen: list[tuple[str, Path]] = []
    monitor.missing.connect(lambda view, path: seen.append((view, path)))
    monitor.track("a", source)
    monitor.check_now()
    monitor.check_now()
    assert seen == []
    monitor.clear()


def test_forget_stops_monitoring(qapp: QApplication, tmp_path: Path) -> None:
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"x")
    monitor = SourceMonitor(poll_ms=60_000, confirm_misses=1)
    seen: list[str] = []
    monitor.missing.connect(lambda view, path: seen.append(view))
    monitor.track("a", source)
    monitor.forget("a")
    source.unlink()
    monitor.check_now()
    assert seen == []
    assert monitor.path_for("a") is None
    monitor.clear()
