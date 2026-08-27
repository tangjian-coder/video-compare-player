"""Tests for core data models."""

from pathlib import Path

import pytest

from vcplayer.core.models import SyncState, VideoMeta


def test_offset_zero_when_incomplete() -> None:
    assert SyncState().offset == 0.0
    assert SyncState(point_a=1.0).offset == 0.0
    assert SyncState(point_b=1.0).offset == 0.0


def test_is_complete() -> None:
    assert not SyncState().is_complete
    assert not SyncState(point_a=1.0).is_complete
    assert SyncState(point_a=1.0, point_b=2.0).is_complete


def test_offset_complete() -> None:
    assert SyncState(point_a=2.0, point_b=5.5).offset == pytest.approx(3.5)
    assert SyncState(point_a=5.5, point_b=2.0).offset == pytest.approx(-3.5)


def test_frame_count() -> None:
    meta = VideoMeta(path=Path("a.mp4"), duration=10.0, fps=120.0, width=1920, height=1080)
    assert meta.frame_count == 1200
