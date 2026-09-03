"""Screen recording of the two video views via an ffmpeg subprocess.

Records the on-screen region covered by the side-by-side video area
(no timeline / transport / status bar) so aligned A/B playback can be
captured as clean demonstration clips.

Design:
- ffmpeg runs as an independent process (QProcess): capture + encode
  never occupy the Python thread pool or the GIL, leaving the
  latency-sensitive sync regulation loop untouched.
- ddagrab + NVENC hardware encoding when available, otherwise
  gdigrab + libx264 (portable, works everywhere).
- Stop is graceful ("q" on stdin) with a force-kill fallback and a
  remux repair path for a damaged mp4 tail.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)

# Stop sequence: wait up to STOP_TIMEOUT_S for ffmpeg to finalize the
# mp4 tail, then force-kill and attempt a remux repair.
STOP_TIMEOUT_S = 5.0
# Even dimensions: many encoders need aligned stride.
_REGION_ALIGN = 2


class RecorderError(Exception):
    """Raised when recording cannot start (no ffmpeg, bad region, ...)."""


class RecorderState(Enum):
    """Lifecycle of a recording session."""

    IDLE = "idle"
    RECORDING = "recording"
    STOPPING = "stopping"
    FINISHED = "finished"
    FAILED = "failed"


@dataclass(frozen=True)
class EncoderProfile:
    """One capture+encode pipeline variant."""

    name: str
    capture: str  # "ddagrab" | "gdigrab"
    encoder: str  # "h264_nvenc" | "libx264"
    encode_args: tuple[str, ...]
    fps: int


_NVENC_PROFILE = EncoderProfile(
    name="ddagrab-nvenc",
    capture="ddagrab",
    encoder="h264_nvenc",
    encode_args=("-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23"),
    fps=60,
)

_DDAGRAB_X264_PROFILE = EncoderProfile(
    name="ddagrab-x264",
    capture="ddagrab",
    encoder="libx264",
    encode_args=("-c:v", "libx264", "-preset", "veryfast", "-crf", "18"),
    fps=30,
)

_GDIGRAB_PROFILE = EncoderProfile(
    name="gdigrab-x264",
    capture="gdigrab",
    encoder="libx264",
    encode_args=("-c:v", "libx264", "-preset", "veryfast", "-crf", "18"),
    fps=30,
)


@dataclass(frozen=True)
class CaptureSupport:
    """What this machine can capture, split by screen position.

    ddagrab addresses DDA output 0 (the primary display) only, so an
    off-primary window needs the gdigrab pipeline. Some minimal ffmpeg
    builds (imageio-ffmpeg) lack gdigrab entirely: off_primary is then
    None and recording is refused off-primary.
    """

    primary: EncoderProfile
    off_primary: EncoderProfile | None


# Under pythonw (no console) each console subprocess would pop up its
# own cmd window; CREATE_NO_WINDOW suppresses that everywhere.
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run_capture(ffmpeg: Path, args: list[str]) -> str:
    """Run ffmpeg with -version-style probes, returning combined output."""
    res = subprocess.run(
        [str(ffmpeg), *args],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        creationflags=_NO_WINDOW,
    )
    return res.stdout + res.stderr


def _dry_run(ffmpeg: Path, args: list[str]) -> bool:
    """Run a probe command; True on exit code 0."""
    try:
        res = subprocess.run(
            [str(ffmpeg), *args],
            capture_output=True,
            timeout=15,
            check=False,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg dry-run failed: %s", exc)
        return False
    return res.returncode == 0


def _dda_dry_run_works(ffmpeg: Path) -> bool:
    """Desktop Duplication available? (Fails in some RDP/headless sessions.)

    One 256x256 frame straight to null: no encoder involved, so this
    isolates capture availability from encoder availability.
    """
    ok = _dry_run(
        ffmpeg,
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "ddagrab=output_idx=0:framerate=30:video_size=256x256",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
    )
    if not ok:
        logger.info("ddagrab dry-run rejected; DDA capture unavailable")
    return ok


def _nvenc_dry_run_works(ffmpeg: Path) -> bool:
    """NVENC truly usable, not merely compiled in?

    Windows ffmpeg builds list h264_nvenc unconditionally; on machines
    without a working NVIDIA runtime the encoder fails only at session
    init. One 256x256 frame through ddagrab->nvenc proves the whole
    hardware path. (256x256: NVENC rejects dimensions below ~145x49.)
    """
    ok = _dry_run(
        ffmpeg,
        [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "ddagrab=output_idx=0:framerate=30:video_size=256x256",
            "-frames:v",
            "1",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "-",
        ],
    )
    if not ok:
        logger.info("nvenc dry-run rejected; falling back to software encoding")
    return ok


# Probing spawns subprocesses; cache per ffmpeg binary.
_support_cache: dict[str, CaptureSupport | None] = {}


def probe_capabilities(ffmpeg: Path) -> CaptureSupport | None:
    """Pick the best capture+encode pipelines this machine supports."""
    key = str(ffmpeg)
    if key in _support_cache:
        return _support_cache[key]
    try:
        encoders = _run_capture(ffmpeg, ["-hide_banner", "-encoders"])
        filters = _run_capture(ffmpeg, ["-hide_banner", "-filters"])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("ffmpeg probe failed: %s", exc)
        return None
    has_ddagrab = "ddagrab" in filters
    has_gdigrab = "gdigrab" in filters
    has_nvenc = "h264_nvenc" in encoders
    has_x264 = "libx264" in encoders
    primary: EncoderProfile | None = None
    if has_ddagrab and has_nvenc and _nvenc_dry_run_works(ffmpeg):
        primary = _NVENC_PROFILE
    elif has_ddagrab and has_x264 and _dda_dry_run_works(ffmpeg):
        primary = _DDAGRAB_X264_PROFILE
    elif has_gdigrab and has_x264:
        primary = _GDIGRAB_PROFILE
    off_primary = _GDIGRAB_PROFILE if has_gdigrab and has_x264 else None
    support = CaptureSupport(primary=primary, off_primary=off_primary) if primary else None
    if support is None:
        logger.warning(
            "no usable capture pipeline (ddagrab=%s nvenc=%s gdigrab=%s x264=%s)",
            has_ddagrab,
            has_nvenc,
            has_gdigrab,
            has_x264,
        )
    _support_cache[key] = support
    return support


def warm_up_probe() -> None:
    """Pre-populate the probe cache so the first Rec click is instant.

    Spawning probes costs ~1s of subprocess latency; run this in a
    background thread right after startup, before the user clicks Rec.
    """
    ffmpeg = locate_ffmpeg()
    if ffmpeg is not None:
        probe_capabilities(ffmpeg)


def locate_ffmpeg() -> Path | None:
    """Locate an ffmpeg binary: bundled (frozen) first, then venv, then PATH."""
    # Frozen build: ffmpeg.exe was bundled next to the app resources.
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / "ffmpeg.exe"
            if bundled.is_file():
                return bundled
    # Dev: venv's imageio-ffmpeg binary (an explicit project dependency).
    with contextlib.suppress(ImportError, RuntimeError):
        import imageio_ffmpeg

        return Path(imageio_ffmpeg.get_ffmpeg_exe())
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


@dataclass(frozen=True)
class Region:
    """Physical-pixel screen region in desktop coordinates."""

    x: int
    y: int
    w: int
    h: int

    def as_ffmpeg_geom(self) -> str:
        """Region as ffmpeg crop expression."""
        return f"{self.w}:{self.h}:{self.x}:{self.y}"


def align_region(x: float, y: float, w: float, h: float) -> Region:
    """Round and align raw physical metrics to a valid capture region.

    Input may carry fractional pixels (devicePixelRatio scaling);
    output is clamped positive and even-aligned for encoder stride.
    """
    rx = round(x)
    ry = round(y)
    rw = max(_REGION_ALIGN, int(w) - int(w) % _REGION_ALIGN)
    rh = max(_REGION_ALIGN, int(h) - int(h) % _REGION_ALIGN)
    return Region(x=rx, y=ry, w=rw, h=rh)


def physical_region(widget: QWidget) -> Region:
    """Compute a widget's on-screen region in physical pixels.

    Qt logical coordinates are scaled by devicePixelRatio and re-based
    from the screen origin onto virtual-desktop coordinates (gdigrab/
    ddagrab both capture the whole desktop).
    """
    geom = widget.geometry()
    origin = widget.mapToGlobal(geom.topLeft())
    dpr = widget.devicePixelRatioF()
    screen = widget.screen()
    sorigin = screen.geometry().topLeft()
    # Desktop origin = screen origin + scaled offset within the screen.
    x = sorigin.x() + (origin.x() - sorigin.x()) * dpr
    y = sorigin.y() + (origin.y() - sorigin.y()) * dpr
    w = geom.width() * dpr
    h = geom.height() * dpr
    return align_region(x, y, w, h)


ScreenRect = tuple[int, int, int, int]  # x, y, w, h in physical pixels


def screen_physical_rects() -> list[ScreenRect]:
    """Physical-pixel rects of all screens on the virtual desktop."""
    from PySide6.QtGui import QGuiApplication

    rects: list[ScreenRect] = []
    for scr in QGuiApplication.screens():
        g = scr.geometry()
        dpr = scr.devicePixelRatio()
        rects.append((g.x(), g.y(), round(g.width() * dpr), round(g.height() * dpr)))
    return rects


def region_within(region: Region, rect: ScreenRect) -> bool:
    """True when the region lies fully inside one physical screen rect."""
    sx, sy, sw, sh = rect
    return (
        region.x >= sx
        and region.y >= sy
        and region.x + region.w <= sx + sw
        and region.y + region.h <= sy + sh
    )


def region_fully_on_screens(region: Region, screens: list[ScreenRect]) -> bool:
    """True when all four region corners fall inside some screen.

    Guards against minimized windows (Win32 parks them at -32000) and
    half-off-screen regions that ffmpeg cannot capture.
    """
    corners = (
        (region.x, region.y),
        (region.x + region.w - 1, region.y),
        (region.x, region.y + region.h - 1),
        (region.x + region.w - 1, region.y + region.h - 1),
    )
    for cx, cy in corners:
        if not any(sx <= cx < sx + sw and sy <= cy < sy + sh for sx, sy, sw, sh in screens):
            return False
    return True


def clamp_region_to_desktop(region: Region, screens: list[ScreenRect]) -> Region:
    """Shrink overhanging region edges onto the virtual desktop.

    A maximized window's client area extends a few px past the screen
    on every side (Windows "hanging" borders): clip those invisible
    slivers off so region checks pass.
    """
    min_x = min(s[0] for s in screens)
    min_y = min(s[1] for s in screens)
    max_x = max(s[0] + s[2] for s in screens)
    max_y = max(s[1] + s[3] for s in screens)
    x, y, w, h = region.x, region.y, region.w, region.h
    if x < min_x:
        w -= min_x - x
        x = min_x
    if y < min_y:
        h -= min_y - y
        y = min_y
    if x + w > max_x:
        w = max_x - x
    if y + h > max_y:
        h = max_y - y
    w = max(_REGION_ALIGN, w - w % _REGION_ALIGN)
    h = max(_REGION_ALIGN, h - h % _REGION_ALIGN)
    return Region(x=x, y=y, w=w, h=h)


def default_output_dir() -> Path:
    """User's Videos library, with a vcplayer subfolder."""
    return Path.home() / "Videos" / "vcplayer"


def unique_output_path(directory: Path, timestamp: str) -> Path:
    """compare_<ts>.mp4, with _2/_3/... suffix when the name is taken.

    The timestamp has second resolution; a fast stop-restart cycle can
    otherwise silently overwrite the previous clip via ffmpeg -y.
    """
    candidate = directory / f"compare_{timestamp}.mp4"
    n = 2
    while candidate.exists():
        candidate = directory / f"compare_{timestamp}_{n}.mp4"
        n += 1
    return candidate


def output_timestamp() -> str:
    """Timestamp used in output filenames, second resolution."""
    return time.strftime("%Y%m%d_%H%M%S")


def build_args(profile: EncoderProfile, region: Region, out: Path) -> list[str]:
    """Assemble the ffmpeg argument list for one recording session."""
    if profile.capture == "ddagrab":
        head = (
            f"ddagrab=output_idx=0:framerate={profile.fps}:draw_mouse=1"
            f":video_size={region.w}x{region.h}"
            f":offset_x={region.x}:offset_y={region.y}"
        )
        if profile.encoder == "h264_nvenc":
            # Hardware end-to-end: D3D11 frames straight to NVENC. No
            # software -pix_fmt (it would force a hwdownload); main
            # profile pins H.264 4:2:0 for player compatibility.
            return [
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                head,
                "-r",
                str(profile.fps),
                "-fps_mode",
                "cfr",
                *profile.encode_args,
                "-profile:v",
                "main",
                "-movflags",
                "+faststart",
                "-y",
                str(out),
            ]
        # DDA capture + CPU encode: pull frames back into system memory.
        return [
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{head},hwdownload,format=bgra",
            "-fps_mode",
            "cfr",
            *profile.encode_args,
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            "-y",
            str(out),
        ]
    return [
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "gdigrab",
        "-framerate",
        str(profile.fps),
        "-offset_x",
        str(region.x),
        "-offset_y",
        str(region.y),
        "-video_size",
        f"{region.w}x{region.h}",
        "-draw_mouse",
        "1",
        "-i",
        "desktop",
        "-fps_mode",
        "cfr",
        *profile.encode_args,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y",
        str(out),
    ]


def repair_mp4(ffmpeg: Path, path: Path) -> bool:
    """Remux a file whose tail was damaged by a force-kill.

    Only helps when ffmpeg got far enough to write the moov atom; a
    hard-killed file without moov is unrecoverable (returns False).
    """
    if not path.is_file():
        return False
    fixed = path.with_suffix(".fixed.mp4")
    res = subprocess.run(
        [
            str(ffmpeg),
            "-y",
            "-err_detect",
            "ignore_err",
            "-i",
            str(path),
            "-c",
            "copy",
            str(fixed),
        ],
        capture_output=True,
        timeout=60,
        check=False,
        creationflags=_NO_WINDOW,
    )
    if res.returncode == 0 and fixed.is_file() and fixed.stat().st_size > 0:
        try:
            fixed.replace(path)
            return True
        except OSError:
            logger.warning("could not replace damaged clip with repaired copy")
    with contextlib.suppress(OSError):
        fixed.unlink()
    return False


class ScreenRecorder(QObject):
    """Owns the ffmpeg QProcess across recording sessions.

    A single instance is reused for every session: the state machine
    supports restart from FINISHED/FAILED, and per-session profiles can
    be passed to start() (primary-screen nvenc vs off-screen x264).
    """

    stateChanged = Signal(str)
    error = Signal(str)
    saved = Signal(str)

    def __init__(
        self, ffmpeg: Path, profile: EncoderProfile, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self._ffmpeg = Path(ffmpeg)
        self._profile = profile
        self._proc: QProcess | None = None
        self._state = RecorderState.IDLE
        self._out: Path | None = None
        self._t0 = 0.0
        self._stop_deadline: float | None = None
        self._crashed = False
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self.force_stop_watchdog)

    # -- properties --------------------------------------------------------

    @property
    def state(self) -> RecorderState:
        return self._state

    @property
    def output(self) -> Path | None:
        return self._out

    @property
    def recording(self) -> bool:
        return self._state in (RecorderState.RECORDING, RecorderState.STOPPING)

    def elapsed_seconds(self) -> float:
        """Seconds since start; 0 when idle."""
        if self._t0 == 0.0:
            return 0.0
        return max(0.0, time.monotonic() - self._t0)

    # -- state machine ------------------------------------------------------

    def _set_state(self, new_state: RecorderState) -> None:
        if new_state is not self._state:
            self._state = new_state
            self.stateChanged.emit(new_state.value)
            logger.info("recorder state -> %s", new_state.value)

    def start(
        self,
        region: Region,
        out_file: Path,
        profile: EncoderProfile | None = None,
    ) -> None:
        """Launch ffmpeg capturing the given physical region."""
        if self.recording:
            raise RecorderError("already recording")
        if region.w < _REGION_ALIGN or region.h < _REGION_ALIGN:
            raise RecorderError(f"region too small: {region}")
        try:
            out_file.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RecorderError(f"cannot create output directory: {exc}") from exc
        if profile is not None:
            self._profile = profile
        self._out = out_file
        self._crashed = False
        proc = QProcess(self)
        proc.setProgram(str(self._ffmpeg))
        proc.setArguments(build_args(self._profile, region, out_file))
        proc.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_output)
        proc.errorOccurred.connect(self._on_proc_error)
        proc.finished.connect(self._on_proc_finished)
        proc.start()
        if not proc.waitForStarted(3000):
            proc.deleteLater()
            raise RecorderError(f"ffmpeg failed to start: {proc.errorString()}")
        self._proc = proc
        self._t0 = time.monotonic()
        self._set_state(RecorderState.RECORDING)

    def stop(self) -> None:
        """Graceful stop: send 'q', finalize; watchdog force-kills on timeout."""
        proc = self._proc
        if proc is None or self._state is not RecorderState.RECORDING:
            return
        self._set_state(RecorderState.STOPPING)
        proc.write(b"q")
        proc.closeWriteChannel()
        self._stop_deadline = time.monotonic() + STOP_TIMEOUT_S
        self._watchdog.start(int(STOP_TIMEOUT_S * 1000) + 250)

    def stop_timeout_expired(self) -> bool:
        """True when a pending graceful stop exceeded its deadline."""
        return (
            self._state is RecorderState.STOPPING
            and self._stop_deadline is not None
            and time.monotonic() > self._stop_deadline
        )

    def force_kill(self) -> None:
        """Kill a stuck ffmpeg; the mp4 tail may then need repair."""
        proc = self._proc
        if proc is not None and proc.state() is not QProcess.ProcessState.NotRunning:
            logger.warning("ffmpeg stop timed out; killing")
            proc.kill()

    def terminate(self) -> None:
        """Synchronous best-effort stop (closeEvent path)."""
        if self._state is RecorderState.RECORDING:
            self.stop()
        proc = self._proc
        if proc is not None:
            proc.waitForFinished(int(STOP_TIMEOUT_S * 1000))
            if proc.state() is not QProcess.ProcessState.NotRunning:
                proc.kill()
                proc.waitForFinished(2000)

    # -- process events ------------------------------------------------------

    def _on_output(self) -> None:
        proc = self._proc
        if proc is not None:
            data = bytes(proc.readAllStandardOutput().data()).decode("utf-8", "replace")
            logger.debug("[ffmpeg] %s", data.strip()[-400:])

    def _on_proc_error(self, err: QProcess.ProcessError) -> None:
        # Record only: errorOccurred(Crashed) fires BEFORE finished(); the
        # final verdict (and any user-facing signal) belongs to
        # _on_proc_finished so a crash never produces two error paths.
        if err is QProcess.ProcessError.Crashed:
            self._crashed = True
            logger.warning("ffmpeg process crashed")

    def _on_proc_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            proc.deleteLater()
        # A raise here would leave the state stuck in STOPPING and the
        # recorder permanently unusable; any failure becomes FAILED.
        try:
            self._verdict(code)
        except Exception:
            logger.exception("recorder verdict failed")
            self._set_state(RecorderState.FAILED)
            self.error.emit("recording finalization error")

    def _verdict(self, code: int) -> None:
        """Single decision point after ffmpeg exits."""
        out = self._out
        graceful = self._state is RecorderState.STOPPING
        if code == 0:
            self._set_state(RecorderState.FINISHED)
            if out is not None and out.is_file():
                self.saved.emit(str(out))
            else:
                self._set_state(RecorderState.FAILED)
                self.error.emit("ffmpeg exited without writing output")
            return
        # Non-zero exit: try to salvage what was captured.
        salvaged = (
            out is not None
            and out.is_file()
            and out.stat().st_size > 4096
            and repair_mp4(self._ffmpeg, out)
        )
        if salvaged:
            self._set_state(RecorderState.FINISHED)
            self.saved.emit(str(out))
        else:
            with contextlib.suppress(OSError):
                if out is not None:
                    out.unlink()
            self._set_state(RecorderState.FAILED)
            if self._crashed:
                reason = "ffmpeg crashed"
            elif graceful:
                reason = "finalization failed"
            else:
                reason = "ffmpeg failed"
            self.error.emit(reason)

    def force_stop_watchdog(self) -> None:
        """Watchdog tick: force-kill when the graceful stop is stuck."""
        if self.stop_timeout_expired():
            self.force_kill()
