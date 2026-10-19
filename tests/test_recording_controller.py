"""Tests for the recording session controller (no ffmpeg process)."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QWidget

from vcplayer.core import recorder as rec
from vcplayer.ui.recording_controller import RecordingController


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


class FakeRecorder:
    def __init__(self, state: rec.RecorderState) -> None:
        self.state = state
        self.recording = state in (rec.RecorderState.RECORDING, rec.RecorderState.STOPPING)
        self.output = None
        self.stopped = 0
        self.terminated = 0

    def stop(self) -> None:
        self.stopped += 1

    def terminate(self) -> None:
        self.terminated += 1

    def elapsed_seconds(self) -> float:
        return 12.0


def make_controller(qapp: QApplication) -> RecordingController:
    return RecordingController(QWidget(), QWidget())


def test_toggle_stops_a_running_recorder(qapp: QApplication) -> None:
    controller = make_controller(qapp)
    fake = FakeRecorder(rec.RecorderState.RECORDING)
    controller._recorder = fake  # type: ignore[assignment]
    assert controller.recording
    controller.toggle()
    assert fake.stopped == 1
    assert controller.elapsed_seconds() == 12.0


def test_terminate_is_idempotent_without_recorder(qapp: QApplication) -> None:
    controller = make_controller(qapp)
    controller.terminate()


def test_missing_ffmpeg_reports_message(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller = make_controller(qapp)
    monkeypatch.setattr(rec, "locate_ffmpeg", lambda: None)
    messages: list[str] = []
    controller.message.connect(messages.append)
    controller.start()
    assert messages == ["Recording unavailable: ffmpeg not found"]
