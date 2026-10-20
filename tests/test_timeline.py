"""Tests for the multi-track timeline's coordinate mapping and state."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before QApplication

import pytest
from PySide6.QtWidgets import QApplication

from vcplayer.ui.timeline import (
    TRACK_H,
    TRACK_PITCH,
    TRACK_TOP_Y,
    TimelineWidget,
    track_x_to_master,
    tracks_height,
)

# -- track_x_to_master --------------------------------------------------------


def test_a_track_is_identity() -> None:
    assert track_x_to_master(50.0, 100.0, 40.0, "a", 2.0) == pytest.approx(20.0)


def test_b_track_subtracts_offset() -> None:
    # Same pixel on the B track maps one offset earlier in master time.
    assert track_x_to_master(50.0, 100.0, 40.0, "b", 2.0) == pytest.approx(18.0)


def test_negative_offset_adds_for_b() -> None:
    assert track_x_to_master(50.0, 100.0, 40.0, "b", -3.0) == pytest.approx(23.0)


def test_follower_tracks_c_and_d_subtract_offset() -> None:
    assert track_x_to_master(50.0, 100.0, 40.0, "c", 1.0) == pytest.approx(19.0)
    assert track_x_to_master(50.0, 100.0, 40.0, "d", -0.5) == pytest.approx(20.5)


def test_reference_track_moves_with_master_switch() -> None:
    assert track_x_to_master(50.0, 100.0, 40.0, "b", 2.0, "b") == pytest.approx(20.0)
    assert track_x_to_master(50.0, 100.0, 40.0, "a", 2.0, "b") == pytest.approx(18.0)


def test_x_clamped_to_track_width() -> None:
    assert track_x_to_master(150.0, 100.0, 40.0, "a", 0.0) == pytest.approx(40.0)
    assert track_x_to_master(-10.0, 100.0, 40.0, "a", 0.0) == pytest.approx(0.0)


def test_degenerate_span_or_width_yields_zero() -> None:
    assert track_x_to_master(50.0, 100.0, 0.0, "a", 1.0) == 0.0
    assert track_x_to_master(50.0, 0.0, 40.0, "a", 1.0) == 0.0


# -- layout --------------------------------------------------------------------


def test_height_scales_with_track_count() -> None:
    assert tracks_height(2) == 44  # matches the historical dual-track height
    assert tracks_height(3) == 56
    assert tracks_height(4) == 68
    assert tracks_height(4) - tracks_height(3) == 12  # one TRACK_PITCH per view


# -- widget state ---------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def widget(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> TimelineWidget:
    """A TimelineWidget without Qt paint/resize side effects."""
    monkeypatch.setattr(TimelineWidget, "update", lambda self: None)
    monkeypatch.setattr(TimelineWidget, "setMinimumHeight", lambda self, h: None)
    monkeypatch.setattr(TimelineWidget, "width", lambda self: 100)
    return TimelineWidget()


def test_offset_comes_from_set_offsets(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b", "c"])
    widget.set_anchors({"a": 1.0, "b": 2.0, "c": 0.5})
    widget.set_offsets({"b": 1.0, "c": -0.5})
    assert widget._offset("b") == pytest.approx(1.0)
    assert widget._offset("c") == pytest.approx(-0.5)
    assert widget._offset("a") == 0.0


def test_offset_defaults_to_zero_when_unset(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b"])
    assert widget._offset("b") == 0.0


def test_set_offsets_ignores_master_entry(widget: TimelineWidget) -> None:
    widget.set_offsets({"a": 9.0, "b": 1.0})
    assert widget._offset("a") == 0.0
    assert widget._offset("b") == pytest.approx(1.0)
    widget.set_master("b")
    widget.set_offsets({"a": 2.0, "b": 9.0})
    assert widget._offset("b") == 0.0
    assert widget._offset("a") == pytest.approx(2.0)


def test_scrub_map_uses_promoted_reference(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b", "c"])
    widget.set_durations({"a": 40.0, "b": 40.0, "c": 40.0})
    widget.set_master("b")
    widget.set_offsets({"a": 2.0, "c": -1.0})
    master = 24.0 / 62.0 * 40.0
    assert widget._x_to_master(50.0, "b") == pytest.approx(master)
    assert widget._x_to_master(50.0, "a") == pytest.approx(master - 2.0)
    assert widget._x_to_master(50.0, "c") == pytest.approx(master + 1.0)


def test_set_tracks_clears_offsets_of_dropped_views(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b", "c"])
    widget.set_offsets({"b": 1.0, "c": 2.0})
    widget.set_tracks(["a", "b"])
    assert "c" not in widget._offsets
    assert widget._offset("b") == pytest.approx(1.0)


def test_set_anchors_none_clears_that_anchor(widget: TimelineWidget) -> None:
    widget.set_anchors({"a": 1.0, "b": 2.0})
    widget.set_anchors({"a": None, "b": 2.0})
    assert widget._anchor["a"] is None
    assert widget._anchor["b"] == pytest.approx(2.0)


def test_span_is_longest_duration(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b", "c"])
    widget.set_durations({"a": 10.0, "b": 100.0, "c": 50.0})
    assert widget._span == pytest.approx(100.0)


def test_nonpositive_duration_means_unloaded(widget: TimelineWidget) -> None:
    widget.set_durations({"a": 0.0, "b": -5.0})
    assert widget._dur["a"] is None and widget._dur["b"] is None
    assert widget._span == 0.0


def test_set_tracks_preserves_surviving_state(widget: TimelineWidget) -> None:
    widget.set_durations({"a": 10.0, "b": 12.0})
    widget.set_anchors({"a": 1.0, "b": 2.0})
    widget.set_playheads({"a": 3.0, "b": 4.0})
    widget.set_tracks(["a", "b", "c"])
    assert widget._dur["a"] == 10.0
    assert widget._dur["b"] == 12.0
    assert widget._dur["c"] is None
    assert widget._anchor["b"] == 2.0
    assert widget._t["a"] == 3.0
    widget.set_tracks(["a"])  # dropping B discards its state
    assert "b" not in widget._dur
    assert widget._views == ["a"]


def test_view_at_hits_nearest_track(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b"])
    assert widget._view_at(5.0) == "a"
    assert widget._view_at(21.9) == "a"
    assert widget._view_at(22.0) == "b"
    assert widget._view_at(60.0) == "b"  # clamped past the last track
    widget.set_tracks(["a", "b", "c", "d"])
    assert widget._view_at(16.0) == "a"
    assert widget._view_at(30.0) == "b"
    assert widget._view_at(40.0) == "c"
    assert widget._view_at(70.0) == "d"


def test_scrub_map_uses_per_view_offset(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b", "c"])
    widget.set_durations({"a": 40.0, "b": 40.0, "c": 40.0})
    widget.set_offsets({"b": 1.0, "c": -0.5})
    # Fixture width() = 100, with a 26px badge gutter and 12px right pad:
    # x=50 maps through 24/62 of the 40s span (same pixel for all views,
    # each shifted by its own offset).
    assert widget._x_to_master(50.0, "a") == pytest.approx(24.0 / 62.0 * 40.0)
    assert widget._x_to_master(50.0, "b") == pytest.approx(24.0 / 62.0 * 40.0 - 1.0)
    assert widget._x_to_master(50.0, "c") == pytest.approx(24.0 / 62.0 * 40.0 + 0.5)


def test_hover_at_only_hits_track_rows(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b"])
    widget.set_durations({"a": 10.0, "b": 10.0})
    first = TRACK_TOP_Y + TRACK_H / 2
    second = first + TRACK_PITCH
    assert widget._hover_at(first) == "a"
    assert widget._hover_at(second) == "b"
    assert widget._hover_at(1.0) is None
    assert widget._hover_at(1000.0) is None


def test_hover_ignores_unloaded_tracks(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b", "c"])
    widget.set_durations({"a": 10.0, "b": 10.0})
    third = TRACK_TOP_Y + 2 * TRACK_PITCH + TRACK_H / 2
    assert widget._hover_at(third) is None
    widget.set_hovered_view("c")
    assert widget.hovered_view_id is None


def test_hover_clears_when_track_becomes_unloaded(widget: TimelineWidget) -> None:
    widget.set_tracks(["a", "b"])
    widget.set_durations({"a": 10.0, "b": 10.0})
    seen: list[str | None] = []
    widget.hovered_view.connect(seen.append)
    widget.set_hovered_view("b")
    widget.set_durations({"b": None})
    assert widget.hovered_view_id is None
    assert seen == [None]
