"""Tests for core data models."""

import pytest

from vcplayer.core.models import SyncState


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
