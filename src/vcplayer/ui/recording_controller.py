"""Recording session controller: owns the ffmpeg ScreenRecorder lifecycle."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from ..core import recorder as rec

logger = logging.getLogger(__name__)


class RecordingController(QObject):
    """Probe/start/stop/finalize recording for the pane region.

    UI presentation (button text, guard flags, dialogs) stays in MainWindow
    and is driven by the signals emitted here.
    """

    state_changed = Signal(str)
    saved = Signal(str)
    error = Signal(str)
    message = Signal(str)

    def __init__(
        self,
        window: QWidget,
        region_widget: QWidget,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._window = window
        self._region_widget = region_widget
        self._recorder: rec.ScreenRecorder | None = None

    @property
    def recording(self) -> bool:
        """True while recording or finalizing (STOPPING)."""
        return self._recorder is not None and self._recorder.recording

    @property
    def state(self) -> rec.RecorderState | None:
        return self._recorder.state if self._recorder is not None else None

    @property
    def output_name(self) -> str | None:
        recorder = self._recorder
        if recorder is not None and recorder.output is not None:
            return recorder.output.name
        return None

    def elapsed_seconds(self) -> float:
        return self._recorder.elapsed_seconds() if self._recorder is not None else 0.0

    def toggle(self) -> None:
        if self.recording:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        """Probe ffmpeg and launch a capture of the views' screen region."""
        ffmpeg = rec.locate_ffmpeg()
        if ffmpeg is None:
            self.message.emit("Recording unavailable: ffmpeg not found")
            return
        support = rec.probe_capabilities(ffmpeg)
        if support is None:
            logger.warning("recording unavailable: no capture pipeline")
            self.message.emit("Recording unavailable: no usable capture pipeline")
            return
        if self._window.isMinimized():
            self.message.emit("Restore the window before recording")
            return
        region = rec.physical_region(self._region_widget)
        screens = rec.screen_physical_rects()
        # Clip invisible slivers a maximized window hangs past the screen.
        region = rec.clamp_region_to_desktop(region, screens)
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            self.message.emit("Recording unavailable: no primary screen")
            return
        pg = primary.geometry()
        pdpr = primary.devicePixelRatio()
        prect = (pg.x(), pg.y(), round(pg.width() * pdpr), round(pg.height() * pdpr))
        # ddagrab addresses the primary display only (DDA output 0): the
        # region must sit fully inside the primary screen's physical rect;
        # off-primary needs the gdigrab pipeline, if this build has one.
        profile = support.primary
        if profile.capture == "ddagrab" and not rec.region_within(region, prect):
            if support.off_primary is None:
                self.message.emit("Move the window to the primary screen to record")
                return
            profile = support.off_primary
        # Reject regions with corners off the virtual desktop (window half
        # off-screen, parked at -32000, ...): ffmpeg cannot capture.
        if not rec.region_fully_on_screens(region, screens):
            self.message.emit("Recording region is off-screen; move the window fully onto a screen")
            return
        out = rec.unique_output_path(rec.default_output_dir(), rec.output_timestamp())
        try:
            recorder = self._recorder
            if recorder is None:
                recorder = rec.ScreenRecorder(ffmpeg, profile, self)
                recorder.stateChanged.connect(self.state_changed)
                recorder.error.connect(self.error)
                recorder.saved.connect(self.saved)
                # Assign before start(): the "recording" state signal fires
                # synchronously inside start() and its slot reads the output.
                self._recorder = recorder
            recorder.start(region, out, profile)
        except (rec.RecorderError, OSError, RuntimeError) as exc:
            self.error.emit(str(exc))
            return
        logger.info("recording region %s -> %s", region.as_ffmpeg_geom(), out)

    def stop(self) -> None:
        """Graceful stop; the recorder's own watchdog force-kills hangs."""
        if self._recorder is not None:
            self._recorder.stop()

    def terminate(self) -> None:
        """Synchronous best-effort finalize for the close path."""
        if self._recorder is not None:
            self._recorder.terminate()
