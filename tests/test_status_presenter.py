"""Tests for the pure status formatting and playhead smoothing helpers."""

from __future__ import annotations

import pytest

from vcplayer.ui.status_presenter import PlayheadSmoother, drift_ms, format_live_status


def test_drift_ms_formats_and_handles_none() -> None:
    assert drift_ms(None) == "n/a"
    assert drift_ms(0.00123) == "+1.2ms"
    assert drift_ms(-0.002) == "-2.0ms"


def test_status_without_offsets_reports_zero_offset() -> None:
    readout = format_live_status(12.345, {}, {}, 60.0)
    assert readout.text == "A   12.345s | offset +0.000s | drift n/a | 60fps"
    assert readout.tooltip == ""


def test_status_single_offset_includes_drift() -> None:
    readout = format_live_status(12.345, {"b": 0.04}, {"b": 0.0012}, 60.0)
    assert readout.text == "A   12.345s | offset +0.040s | drift +1.2ms | 60fps"


def test_status_two_offsets_lists_pairs() -> None:
    readout = format_live_status(1.0, {"b": 0.04, "c": -0.12}, {"b": 0.0, "c": 0.0012}, 60.0)
    assert readout.text == "A    1.000s | AB +0.040s (+0.0ms) | AC -0.120s (+1.2ms) | 60fps"
    assert readout.tooltip == ""


def test_status_three_offsets_uses_summary_and_tooltip() -> None:
    offsets = {"b": 0.04, "c": -0.12, "d": 0.2}
    drifts = {"b": 0.0, "c": 0.0012, "d": -0.003}
    readout = format_live_status(2.0, offsets, drifts, 60.0)
    assert "3 synced pairs" in readout.text
    assert "max drift +3.0ms" in readout.text
    assert "AB +0.040s" in readout.tooltip
    assert "AD +0.200s" in readout.tooltip


def test_playhead_smoother_interpolates_and_clamps() -> None:
    now = [0.0]
    smoother = PlayheadSmoother(clock=lambda: now[0])
    assert smoother.update(1.0, False, 2.0, 10.0) == 1.0
    now[0] = 0.5
    assert smoother.update(1.0, True, 2.0, 10.0) == pytest.approx(2.0)
    now[0] = 100.0
    assert smoother.update(1.0, True, 2.0, 10.0) == 10.0
    # A new raw frame rebases instead of jumping ahead.
    assert smoother.update(5.0, True, 2.0, 10.0) == 5.0
