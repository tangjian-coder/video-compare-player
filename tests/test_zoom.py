"""Tests for discrete zoom stepping."""

import pytest

from vcplayer.ui.video_view import ZOOM_STEPS, step_zoom


def test_step_up_from_unity() -> None:
    assert step_zoom(1.0, 1) == pytest.approx(1.1)


def test_step_down_from_unity_clamps() -> None:
    assert step_zoom(1.0, -1) == pytest.approx(1.0)


def test_step_up_at_max_clamps() -> None:
    assert step_zoom(8.0, 1) == pytest.approx(8.0)


def test_float_round_trip_tolerated() -> None:
    # mpv stores zoom as log2; 1.3 comes back as 1.2999999...
    assert step_zoom(1.2999999, 1) == pytest.approx(1.4)
    assert step_zoom(2.4000001, -1) == pytest.approx(2.2)


def test_steps_are_sorted_and_bounded() -> None:
    assert ZOOM_STEPS[0] == 1.0
    assert ZOOM_STEPS[-1] == 8.0
    assert all(ZOOM_STEPS[i] < ZOOM_STEPS[i + 1] for i in range(len(ZOOM_STEPS) - 1))


def test_sequence_covers_expected_stops() -> None:
    for z in (1.0, 1.1, 1.2, 1.5, 2.0, 3.0, 4.0, 8.0):
        assert z in ZOOM_STEPS


def test_step_across_band_boundaries() -> None:
    # 0.1 band -> 0.2 band
    assert step_zoom(1.9, 1) == pytest.approx(2.0)
    assert step_zoom(2.0, 1) == pytest.approx(2.2)
    assert step_zoom(2.2, -1) == pytest.approx(2.0)
    assert step_zoom(2.0, -1) == pytest.approx(1.9)
    # 0.2 band -> 0.5 band
    assert step_zoom(4.0, 1) == pytest.approx(4.5)
    assert step_zoom(4.5, -1) == pytest.approx(4.0)


def test_step_from_mid_gap_snaps_to_nearest_stop() -> None:
    # 2.1 rounds to the 2.0 stop: one step lands on its neighbours.
    assert step_zoom(2.1, 1) == pytest.approx(2.2)
    assert step_zoom(2.1, -1) == pytest.approx(1.9)
    # 4.2 is nearer 4.0 (asymmetric gap).
    assert step_zoom(4.2, 1) == pytest.approx(4.5)
    assert step_zoom(4.2, -1) == pytest.approx(3.8)
