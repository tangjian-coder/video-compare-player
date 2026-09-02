"""Tests for the screen-recorder module (no GUI, no real ffmpeg)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QProcess

from vcplayer.core.recorder import (
    EncoderProfile,
    RecorderError,
    RecorderState,
    Region,
    ScreenRecorder,
    align_region,
    build_args,
    physical_region,
    region_fully_on_screens,
    region_within,
    repair_mp4,
    unique_output_path,
)

# -- Region math ---------------------------------------------------------------


def test_align_region_rounds_and_even_aligns() -> None:
    r = align_region(10.4, 20.6, 1921.7, 1080.9)
    assert (r.x, r.y) == (10, 21)
    assert r.w % 2 == 0
    assert r.h % 2 == 0
    assert r.w == 1920
    assert r.h == 1080


def test_align_region_clamps_to_minimum_even() -> None:
    r = align_region(0, 0, 1, 0)
    assert (r.w, r.h) == (2, 2)


def test_region_geom_string() -> None:
    assert Region(x=5, y=6, w=100, h=50).as_ffmpeg_geom() == "100:50:5:6"


class _FakeScreen:
    def __init__(self, x: int, y: int) -> None:
        from PySide6.QtCore import QRect

        self._geom = QRect(x, y, 1920, 1080)

    def geometry(self) -> object:
        return self._geom


class _FakeWidget:
    """Duck-typed QWidget: 100x50 whose global topleft is (10, 20)."""

    def __init__(self, dpr: float = 2.0, screen_origin: tuple[int, int] = (0, 0)) -> None:
        from PySide6.QtCore import QPoint, QRect

        self._geom = QRect(0, 0, 100, 50)
        self._origin = QPoint(10, 20)  # mapToGlobal adds this
        self._dpr = dpr
        self._screen = _FakeScreen(*screen_origin)

    def geometry(self) -> object:
        return self._geom

    def mapToGlobal(self, point: object) -> object:
        return point + self._origin

    def devicePixelRatioF(self) -> float:
        return self._dpr

    def screen(self) -> object:
        return self._screen


def test_physical_region_scales_by_dpr() -> None:
    r = physical_region(_FakeWidget(dpr=2.0))  # type: ignore[arg-type]
    assert (r.x, r.y) == (20, 40)
    assert (r.w, r.h) == (200, 100)


def test_physical_region_rebases_onto_secondary_screen() -> None:
    # Screen origin (-1920, 0): desktop x must stay negative.
    r = physical_region(_FakeWidget(dpr=1.0, screen_origin=(-1920, 0)))  # type: ignore[arg-type]
    assert r.x == 10
    assert r.y == 20
    assert (r.w, r.h) == (100, 50)


def test_physical_region_secondary_screen_with_dpr_1_5() -> None:
    # Locks the coordinate model on scaled secondary screens: Qt keeps
    # native-pixel origins but logical sizes, so physical = origin +
    # (global - origin) * dpr with sizes scaled by dpr.
    w = _FakeWidget(dpr=1.5, screen_origin=(-1920, 3))  # type: ignore[arg-type]
    # Place the widget's global origin at logical (-1900, 5) on that
    # screen: native x = -1920 + 20*1.5 = -1890, y = 3 + 2*1.5 = 6.
    w._origin.setX(-1900)
    w._origin.setY(5)
    r = physical_region(w)  # type: ignore[arg-type]
    assert r.x == -1890
    assert r.y == 6
    assert r.w == 150  # 100 logical * 1.5
    assert r.h == 74  # 75 raw, even-aligned down


# -- Region-vs-screen validation -------------------------------------------------


def test_region_within_single_screen() -> None:
    assert region_within(Region(10, 10, 100, 50), (0, 0, 1920, 1080))
    assert not region_within(Region(10, 10, 100, 50), (100, 0, 1920, 1080))
    # Exactly touching the right/bottom edge is inside.
    assert region_within(Region(0, 0, 1920, 1080), (0, 0, 1920, 1080))
    assert not region_within(Region(1, 0, 1920, 1080), (0, 0, 1920, 1080))


def test_region_fully_on_screens_across_two_monitors() -> None:
    screens = [(0, 0, 1920, 1080), (1920, 0, 1920, 1080)]
    # Each corner falls inside some screen: allowed (gdigrab can span).
    assert region_fully_on_screens(Region(1800, 100, 240, 50), screens)
    # Corner outside any screen: rejected (half off-screen / minimized).
    assert not region_fully_on_screens(Region(-32000, -32000, 800, 600), screens)
    assert not region_fully_on_screens(Region(3700, 100, 240, 50), screens)


def test_unique_output_path_suffixes_collisions(tmp_path: Path) -> None:
    first = unique_output_path(tmp_path, "20260902_120000")
    first.touch()
    second = unique_output_path(tmp_path, "20260902_120000")
    second.touch()
    third = unique_output_path(tmp_path, "20260902_120000")
    assert first.name == "compare_20260902_120000.mp4"
    assert second.name == "compare_20260902_120000_2.mp4"
    assert third.name == "compare_20260902_120000_3.mp4"


# -- Command construction --------------------------------------------------------


def _profile(capture: str = "gdigrab") -> EncoderProfile:
    return EncoderProfile(
        name=f"{capture}-x264",
        capture=capture,
        encoder="libx264",
        encode_args=("-c:v", "libx264", "-crf", "18"),
        fps=30,
    )


def test_build_args_gdigrab() -> None:
    args = build_args(_profile("gdigrab"), Region(x=8, y=16, w=640, h=360), Path("out.mp4"))
    assert args[args.index("-f") + 1] == "gdigrab"
    assert args[args.index("-offset_x") + 1] == "8"
    assert args[args.index("-offset_y") + 1] == "16"
    assert args[args.index("-video_size") + 1] == "640x360"
    assert args[args.index("-i") + 1] == "desktop"
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "yuv420p"
    assert args[args.index("-movflags") + 1] == "+faststart"
    assert args[-1] == "out.mp4"
    assert "libx264" in args


def test_build_args_ddagrab_native_region() -> None:
    prof = EncoderProfile(
        name="ddagrab-nvenc",
        capture="ddagrab",
        encoder="h264_nvenc",
        encode_args=("-c:v", "h264_nvenc", "-cq", "23"),
        fps=60,
    )
    args = build_args(prof, Region(x=0, y=0, w=1280, h=720), Path("o.mp4"))
    lavfi = args[args.index("-i") + 1]
    assert lavfi.startswith("ddagrab=output_idx=0:framerate=60")
    assert "video_size=1280x720" in lavfi
    assert "offset_x=0" in lavfi and "offset_y=0" in lavfi
    assert "h264_nvenc" in args
    # Hardware frames must not be pulled back into system memory.
    assert "-pix_fmt" not in args
    assert args[args.index("-profile:v") + 1] == "main"


def test_constrain_removed_use_capture_support() -> None:
    """The off-primary decision now lives in CaptureSupport (see below)."""
    from vcplayer.core.recorder import _GDIGRAB_PROFILE, _NVENC_PROFILE, CaptureSupport

    support = CaptureSupport(primary=_NVENC_PROFILE, off_primary=_GDIGRAB_PROFILE)
    assert support.primary.capture == "ddagrab"
    assert support.off_primary is not None
    bare = CaptureSupport(primary=_NVENC_PROFILE, off_primary=None)
    assert bare.off_primary is None


def test_build_args_ddagrab_x264_downloads_frames() -> None:
    """DDA capture + CPU encode must hwdownload into system memory."""
    prof = EncoderProfile(
        name="ddagrab-x264",
        capture="ddagrab",
        encoder="libx264",
        encode_args=("-c:v", "libx264", "-crf", "18"),
        fps=30,
    )
    args = build_args(prof, Region(x=8, y=16, w=640, h=360), Path("o.mp4"))
    lavfi = args[args.index("-i") + 1]
    assert lavfi.startswith("ddagrab=output_idx=0:framerate=30")
    assert lavfi.endswith("hwdownload,format=bgra")
    assert "libx264" in args
    assert args[args.index("-pix_fmt") + 1] == "yuv420p"


# -- State machine (fake process injection) -------------------------------------


class _FakeProc:
    """Minimal QProcess stand-in."""

    def __init__(self) -> None:
        from PySide6.QtCore import QProcess

        self.written: list[bytes] = []
        self.killed = False
        self.waited_ms: list[int] = []
        self._state = QProcess.ProcessState.Running
        self._qprocess = QProcess  # class ref for enum access

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def closeWriteChannel(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True
        self._state = self._qprocess.ProcessState.NotRunning

    def waitForFinished(self, ms: int) -> bool:
        self.waited_ms.append(ms)
        return True

    def state(self) -> object:
        return self._state


@pytest.fixture
def recorder() -> ScreenRecorder:
    return ScreenRecorder(Path("ffmpeg.exe"), _profile())


def test_start_rejects_when_recording(recorder: ScreenRecorder) -> None:
    recorder._state = RecorderState.RECORDING
    with pytest.raises(RecorderError):
        recorder.start(Region(0, 0, 64, 64), Path("o.mp4"))


def test_start_rejects_tiny_region(recorder: ScreenRecorder) -> None:
    with pytest.raises(RecorderError):
        recorder.start(Region(0, 0, 1, 1), Path("o.mp4"))


def test_stop_sends_q(recorder: ScreenRecorder, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeProc()
    monkeypatch.setattr(recorder, "_proc", fake)
    recorder._state = RecorderState.RECORDING
    recorder.stop()
    assert fake.written == [b"q"]
    assert recorder.state is RecorderState.STOPPING


def test_stop_ignores_when_idle(recorder: ScreenRecorder) -> None:
    recorder.stop()  # no proc: must not raise
    assert recorder.state is RecorderState.IDLE


def test_watchdog_kills_stuck_stop(
    recorder: ScreenRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _time

    fake = _FakeProc()
    monkeypatch.setattr(recorder, "_proc", fake)
    recorder._state = RecorderState.STOPPING
    recorder._stop_deadline = _time.monotonic() - 1.0  # expired
    recorder.force_stop_watchdog()
    assert fake.killed


def test_watchdog_noop_when_deadline_not_expired(
    recorder: ScreenRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time as _time

    fake = _FakeProc()
    monkeypatch.setattr(recorder, "_proc", fake)
    recorder._state = RecorderState.STOPPING
    recorder._stop_deadline = _time.monotonic() + 60.0
    recorder.force_stop_watchdog()
    assert not fake.killed


def test_finished_success_emits_saved(
    recorder: ScreenRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"x" * 32)
    recorder._out = out
    recorder._state = RecorderState.STOPPING
    got: list[str] = []
    recorder.saved.connect(got.append)
    recorder._on_proc_finished(0, QProcess.ExitStatus.NormalExit)
    assert got == [str(out)]
    assert recorder.state is RecorderState.FINISHED


def test_finished_zero_exit_without_file_fails(
    recorder: ScreenRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder._out = Path("nonexistent.mp4")
    recorder._state = RecorderState.STOPPING
    errors: list[str] = []
    recorder.error.connect(errors.append)
    recorder._on_proc_finished(0, QProcess.ExitStatus.NormalExit)
    assert errors
    assert recorder.state is RecorderState.FAILED


def test_finished_nonzero_salvages_via_repair(
    recorder: ScreenRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"x" * 8192)
    recorder._out = out
    recorder._state = RecorderState.STOPPING
    monkeypatch.setattr("vcplayer.core.recorder.repair_mp4", lambda ff, p: True)
    got: list[str] = []
    recorder.saved.connect(got.append)
    recorder._on_proc_finished(1, QProcess.ExitStatus.NormalExit)
    assert got == [str(out)]
    assert recorder.state is RecorderState.FINISHED


def test_finished_nonzero_failure_deletes_and_fails(
    recorder: ScreenRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"x" * 8192)
    recorder._out = out
    recorder._state = RecorderState.STOPPING
    monkeypatch.setattr("vcplayer.core.recorder.repair_mp4", lambda ff, p: False)
    errors: list[str] = []
    recorder.error.connect(errors.append)
    recorder._on_proc_finished(1, QProcess.ExitStatus.NormalExit)
    assert errors
    assert recorder.state is RecorderState.FAILED
    assert not out.exists()


def test_elapsed_seconds_idle_is_zero(recorder: ScreenRecorder) -> None:
    assert recorder.elapsed_seconds() == 0.0


# -- repair_mp4 ------------------------------------------------------------------


def test_repair_missing_file_is_false(tmp_path: Path) -> None:
    assert repair_mp4(Path("ffmpeg.exe"), tmp_path / "nope.mp4") is False


# -- Crash / exception paths (review MAJOR findings) ------------------------------


def test_crashed_then_finished_single_verdict(
    recorder: ScreenRecorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """errorOccurred(Crashed) precedes finished(): exactly one error signal.

    _on_proc_error must only set a flag; _on_proc_finished is the sole
    verdict point or a crash yields two dialogs / wrong wording.
    """
    recorder._out = None
    recorder._state = RecorderState.RECORDING
    errors: list[str] = []
    recorder.error.connect(errors.append)
    # Crash first: flag only, no state change, no signal yet.
    recorder._on_proc_error(QProcess.ProcessError.Crashed)
    assert recorder.state is RecorderState.RECORDING
    assert errors == []
    assert recorder._crashed is True
    # Then the finished signal with a crash exit code.
    recorder._on_proc_finished(62097, QProcess.ExitStatus.CrashExit)
    assert errors == ["ffmpeg crashed"]
    assert recorder.state is RecorderState.FAILED


def test_start_rejects_unwritable_output_dir(
    recorder: ScreenRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mkdir failures must surface as RecorderError, not OSError."""
    # A file where the directory should be: mkdir raises NotADirectoryError.
    blocker = tmp_path / "blocker"
    blocker.touch()
    out = blocker / "sub" / "clip.mp4"
    with pytest.raises(RecorderError, match="output directory"):
        recorder.start(Region(0, 0, 64, 64), out)


def test_verdict_exception_forces_failed_not_stuck(
    recorder: ScreenRecorder, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception inside the verdict must not strand the recorder.

    A stranded STOPPING state makes recording() forever True and the
    F9 toggle permanently dead until app restart.
    """
    out = tmp_path / "clip.mp4"
    out.write_bytes(b"x" * 8192)
    recorder._out = out
    recorder._state = RecorderState.STOPPING

    def _boom(ffmpeg: Path, path: Path) -> bool:
        raise TimeoutError("repair timed out")

    monkeypatch.setattr("vcplayer.core.recorder.repair_mp4", _boom)
    errors: list[str] = []
    recorder.error.connect(errors.append)
    recorder._on_proc_finished(1, QProcess.ExitStatus.NormalExit)
    assert errors == ["recording finalization error"]
    assert recorder.state is RecorderState.FAILED
    assert not recorder.recording  # toggle works again
