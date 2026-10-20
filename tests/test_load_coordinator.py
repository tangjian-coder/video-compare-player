"""Tests for asynchronous load polling and failure reporting."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before QApplication

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from vcplayer.core.models import VideoMeta
from vcplayer.ui.load_coordinator import LoadCoordinator


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])  # type: ignore[return-value]


class FakePlayer:
    def __init__(self, meta: VideoMeta | None = None, duration: float | None = None) -> None:
        self.meta = meta
        self.duration = duration
        self.is_failed = False
        self.load_error: str | None = None

    def try_capture_meta(self) -> VideoMeta | None:
        return self.meta

    def mark_load_failed(self, message: str) -> None:
        self.is_failed = True
        self.load_error = message
        self.meta = None


def test_successful_load_is_emitted_once(app: QApplication) -> None:
    meta = VideoMeta(Path("v.mp4"), 10.0, 60.0, 1920, 1080)
    player = FakePlayer(meta=meta)
    loads = LoadCoordinator(timeout_ms=200)
    seen: list[tuple[Any, ...]] = []
    loads.loaded.connect(lambda *args: seen.append(args))
    progress: list[None] = []
    loads.progress.connect(lambda: progress.append(None))
    loads.start("a", Path("v.mp4"), player)  # type: ignore[arg-type]
    loads.poll()
    loads.poll()
    assert len(seen) == 1
    assert seen[0][0] == "a"
    assert seen[0][2] is meta
    assert loads.pending_ids == set()
    assert len(progress) == 1


def test_progress_emits_while_metadata_is_pending(app: QApplication) -> None:
    player = FakePlayer()
    loads = LoadCoordinator(timeout_ms=500)
    progress: list[None] = []
    loads.progress.connect(lambda: progress.append(None))
    loads.start("c", Path("slow.mp4"), player)  # type: ignore[arg-type]
    loads.poll()
    assert loads.pending_ids == {"c"}
    assert len(progress) == 1


def test_timeout_marks_failed_without_a_dialog(app: QApplication) -> None:
    player = FakePlayer()
    loads = LoadCoordinator(tick_ms=100, timeout_ms=100)
    failures: list[tuple[Any, ...]] = []
    loads.failed.connect(lambda *args: failures.append(args))
    loads.start("b", Path("slow.mp4"), player)  # type: ignore[arg-type]
    loads.poll()
    assert loads.pending_ids == {"b"}
    loads.poll()
    assert loads.pending_ids == set()
    assert failures and failures[0][0] == "b"
    assert "Timed out" in failures[0][2]
    assert player.is_failed
