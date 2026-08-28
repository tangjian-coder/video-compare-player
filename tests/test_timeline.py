"""Tests for the dual-track timeline's coordinate mapping and state."""

import pytest

from vcplayer.ui.timeline import TimelineWidget, track_x_to_master

# -- track_x_to_master --------------------------------------------------------


def test_a_track_is_identity() -> None:
    assert track_x_to_master(50.0, 100.0, 40.0, "a", 2.0) == pytest.approx(20.0)


def test_b_track_subtracts_offset() -> None:
    # Same pixel on the B track maps one offset earlier in master time.
    assert track_x_to_master(50.0, 100.0, 40.0, "b", 2.0) == pytest.approx(18.0)


def test_negative_offset_adds_for_b() -> None:
    assert track_x_to_master(50.0, 100.0, 40.0, "b", -3.0) == pytest.approx(23.0)


def test_x_clamped_to_track_width() -> None:
    assert track_x_to_master(150.0, 100.0, 40.0, "a", 0.0) == pytest.approx(40.0)
    assert track_x_to_master(-10.0, 100.0, 40.0, "a", 0.0) == pytest.approx(0.0)


def test_degenerate_span_or_width_yields_zero() -> None:
    assert track_x_to_master(50.0, 100.0, 0.0, "a", 1.0) == 0.0
    assert track_x_to_master(50.0, 0.0, 40.0, "a", 1.0) == 0.0


# -- widget state ---------------------------------------------------------------


@pytest.fixture
def widget(monkeypatch: pytest.MonkeyPatch) -> TimelineWidget:
    """A TimelineWidget without Qt paint side effects."""
    w = TimelineWidget.__new__(TimelineWidget)
    monkeypatch.setattr(TimelineWidget, "update", lambda self: None)
    return w


def test_offset_derived_from_anchors(widget: TimelineWidget) -> None:
    widget.set_durations(10.0, 12.0)
    widget.set_anchors(1.0, 2.0)
    assert widget._offset == pytest.approx(1.0)


def test_partial_anchors_mean_no_offset(widget: TimelineWidget) -> None:
    widget.set_anchors(1.0, 2.0)
    widget.set_anchors(None, 2.0)  # cleared A side
    assert widget._offset == 0.0


def test_span_is_longer_duration(widget: TimelineWidget) -> None:
    widget.set_durations(10.0, 100.0)
    assert widget._span == pytest.approx(100.0)


def test_nonpositive_duration_means_unloaded(widget: TimelineWidget) -> None:
    widget.set_durations(0.0, -5.0)
    assert widget._dur_a is None and widget._dur_b is None
    assert widget._span == 0.0
