"""Tests for optional performance instrumentation."""

from __future__ import annotations

import logging

import pytest

from vcplayer import perf


def test_perf_disabled_does_not_log(caplog: pytest.LogCaptureFixture) -> None:
    perf.configure(False)
    try:
        perf.mark("hidden")
        assert "hidden" not in caplog.text
        assert not perf.enabled()
    finally:
        perf.configure(False)


def test_perf_enabled_logs_elapsed(caplog: pytest.LogCaptureFixture) -> None:
    perf.configure(True)
    try:
        with caplog.at_level(logging.INFO, logger="vcplayer.perf"):
            perf.mark("visible", perf.process_start())
        assert "PERF visible" in caplog.text
        assert perf.enabled()
    finally:
        perf.configure(False)
