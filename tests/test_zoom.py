"""Tests for discrete zoom stepping, paired pan conversion and rotation math."""

import pytest

from vcplayer.ui.video_view import (
    ZOOM_STEPS,
    pixels_to_pan_delta,
    rotated_size,
    step_zoom,
)


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


# -- pixels_to_pan_delta ------------------------------------------------------


def test_pan_delta_basic_conversion() -> None:
    # 100 px on a 1000 px display = 0.1 of the scaled video size.
    assert pixels_to_pan_delta(100.0, -50.0, 1000.0, 500.0) == pytest.approx((0.1, -0.1))


def test_pan_delta_smaller_display_is_larger_fraction() -> None:
    # Same pixels, half-size display: double the fraction (1:1 tracking).
    big = pixels_to_pan_delta(60.0, 60.0, 2000.0, 2000.0)
    small = pixels_to_pan_delta(60.0, 60.0, 1000.0, 1000.0)
    assert small[0] == pytest.approx(2.0 * big[0])
    assert small[1] == pytest.approx(2.0 * big[1])


def test_pan_delta_zero_pixels() -> None:
    assert pixels_to_pan_delta(0.0, 0.0, 800.0, 600.0) == (0.0, 0.0)


def test_pan_delta_degenerate_display_yields_no_movement() -> None:
    assert pixels_to_pan_delta(100.0, 100.0, 0.0, 600.0) == (0.0, 0.0)
    assert pixels_to_pan_delta(100.0, 100.0, 800.0, 0.0) == (0.0, 0.0)


# -- rotated_size -------------------------------------------------------------


def test_rotated_size_identity_at_0_and_180() -> None:
    assert rotated_size(1920.0, 1080.0, 0) == (1920.0, 1080.0)
    assert rotated_size(1920.0, 1080.0, 180) == (1920.0, 1080.0)


def test_rotated_size_swaps_at_90_and_270() -> None:
    assert rotated_size(1920.0, 1080.0, 90) == (1080.0, 1920.0)
    assert rotated_size(1920.0, 1080.0, 270) == (1080.0, 1920.0)


def test_rotated_size_full_turn_is_identity() -> None:
    assert rotated_size(1920.0, 1080.0, 360) == (1920.0, 1080.0)
    assert rotated_size(1920.0, 1080.0, 450) == (1080.0, 1920.0)
