"""Main window: dual video blocks + shared transport + timeline + sync logic.

Each video block (VideoView) has its own frame-step and sync controls, which
is required for the sync-point workflow: step each view independently to the
same physical moment, then mark both sync points.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..core import recorder as rec
from ..core.player import PlayerController, PlayerError
from ..core.sync import SyncEngine, SyncError, ViewId
from .timeline import TimelineWidget
from .video_view import VIDEO_EXTS, VideoView

logger = logging.getLogger(__name__)

SPEED_STEPS = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0]
UI_TICK_MS = 15  # ~66 Hz so the playhead tracks high-fps video smoothly
DRIFT_TICK_MS = 100
LOAD_TICK_MS = 100
LOAD_TIMEOUT_MS = 10_000


class MainWindow(QMainWindow):
    """Top-level window assembling views, transport, timeline and sync logic."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("video-compare-player")
        self.resize(config.window.width, config.window.height)

        self.view_a = VideoView("A")
        self.view_b = VideoView("B")
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.view_a)
        self.splitter.addWidget(self.view_b)
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)

        self.timeline = TimelineWidget()

        self.btn_first = QPushButton("|<")
        self.btn_step_back = QPushButton("<")
        self.btn_play = QPushButton("Play")
        self.btn_play.setObjectName("playButton")
        self.btn_step_fwd = QPushButton(">")
        self.btn_last = QPushButton(">|")
        self.speed_combo = QComboBox()
        for s in SPEED_STEPS:
            self.speed_combo.addItem(f"{s:g}x", s)
        self.speed_combo.setCurrentIndex(
            SPEED_STEPS.index(config.speed) if config.speed in SPEED_STEPS else 3
        )
        self.btn_open_a = QPushButton("Open A")
        self.btn_open_b = QPushButton("Open B")
        self.btn_align = QPushButton("⇄ Align current frames")
        self.btn_align.setObjectName("alignButton")
        self.btn_align.setToolTip(
            "Step each video to the same physical moment (e.g. stopwatch tick),\n"
            "then click this: both current frames become the sync anchor. (S)"
        )
        self.sync_chip = QLabel("● not synced")
        self.sync_chip.setObjectName("syncChip")
        self.sync_chip.setProperty("state", "off")
        self.btn_sync_clear = QPushButton("Clear sync")
        self.btn_sync_clear.setObjectName("syncButton")
        self.btn_rec = QPushButton("⏺ Rec")
        self.btn_rec.setObjectName("recButton")
        self.btn_rec.setToolTip(
            "Record the two video views to an mp4 clip. (F9)\n"
            "Do not move or resize the window while recording."
        )

        transport = QHBoxLayout()
        transport.addStretch(1)
        transport.addWidget(self.btn_open_a)
        transport.addWidget(self.btn_open_b)
        transport.addSpacing(24)
        transport.addWidget(self.btn_first)
        transport.addWidget(self.btn_step_back)
        transport.addWidget(self.btn_play)
        transport.addWidget(self.btn_step_fwd)
        transport.addWidget(self.btn_last)
        transport.addSpacing(24)
        transport.addWidget(QLabel("speed"))
        transport.addWidget(self.speed_combo)
        transport.addSpacing(24)
        transport.addWidget(self.btn_align)
        transport.addWidget(self.sync_chip)
        transport.addSpacing(12)
        transport.addWidget(self.btn_sync_clear)
        transport.addSpacing(12)
        transport.addWidget(self.btn_rec)
        transport.addStretch(1)

        central = QWidget()
        central.setObjectName("centralContainer")
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)
        vbox.addWidget(self.splitter, stretch=1)
        vbox.addWidget(self.timeline)
        vbox.addLayout(transport)
        self.setCentralWidget(central)

        self.engine: SyncEngine | None = None
        self._active_view: ViewId = "a"
        self._load_pending: dict[ViewId, tuple[Path, int]] = {}
        # Playhead smoothing state: interpolate between mpv frame updates.
        self._playhead_raw = 0.0
        self._playhead_anchor = 0.0
        self._playhead_mono = 0.0
        self._playing = False
        self._closing = False
        # Live readout lives in a permanent widget: showMessage() stays free
        # for event messages ("Synced: ..."), which would otherwise be
        # clobbered by the 15 ms UI tick.
        self._status_live = QLabel("")
        self.statusBar().addPermanentWidget(self._status_live)
        self._timeline_durs: tuple[float | None, float | None] | None = None
        # Screen recorder session (created on first F9/Rec click).
        self._recorder: rec.ScreenRecorder | None = None
        self._rec_secs = -1  # last elapsed second shown on the button

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._on_ui_tick)
        self._drift_timer = QTimer(self)
        self._drift_timer.timeout.connect(self._on_drift_tick)
        self._load_timer = QTimer(self)
        self._load_timer.timeout.connect(self._on_load_tick)

    # -- lifecycle --------------------------------------------------------------

    def initialize(self, left: Path | None, right: Path | None) -> None:
        """Create the sync engine and wire signals; call after the window is shown."""
        try:
            self.view_a.ensure_player()
            self.view_b.ensure_player()
            self.engine = SyncEngine(self.view_a.player, self.view_b.player)
        except (RuntimeError, PlayerError) as exc:
            QMessageBox.critical(self, "mpv error", str(exc))
            self.close()  # never leave a dead, unresponsive window on screen
            return

        self._wire_view(self.view_a, "a")
        self._wire_view(self.view_b, "b")
        # Ctrl-modified gestures steer both surfaces together: each side
        # converts zoom steps / pointer deltas with its own metrics.
        self.view_a.surface.paired_zoom.connect(self.view_b.surface.apply_paired_zoom)
        self.view_b.surface.paired_zoom.connect(self.view_a.surface.apply_paired_zoom)
        self.view_a.surface.paired_pan.connect(self.view_b.surface.apply_paired_pan)
        self.view_b.surface.paired_pan.connect(self.view_a.surface.apply_paired_pan)
        self.view_a.surface.paired_reset.connect(self.view_b.surface.apply_paired_reset)
        self.view_b.surface.paired_reset.connect(self.view_a.surface.apply_paired_reset)
        self.timeline.scrub_preview.connect(self._on_scrub_preview)
        self.timeline.seek_committed.connect(self._on_seek_committed)

        self.btn_open_a.clicked.connect(lambda: self._open_dialog("a"))
        self.btn_open_b.clicked.connect(lambda: self._open_dialog("b"))
        self.btn_first.clicked.connect(self._goto_first)
        self.btn_step_back.clicked.connect(lambda: self._step(-1))
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_step_fwd.clicked.connect(lambda: self._step(1))
        self.btn_last.clicked.connect(self._goto_last)
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self.btn_align.clicked.connect(self._align_current_frames)
        self.btn_sync_clear.clicked.connect(self._clear_sync)
        self.btn_rec.clicked.connect(self._toggle_record)

        self._add_shortcut(Qt.Key.Key_Space, self._toggle_play)
        self._add_shortcut(Qt.Key.Key_Left, lambda: self._step(-1))
        self._add_shortcut(Qt.Key.Key_Right, lambda: self._step(1))
        self._add_shortcut(Qt.Key.Key_Up, lambda: self._cycle_speed(1))
        self._add_shortcut(Qt.Key.Key_Down, lambda: self._cycle_speed(-1))
        self._add_shortcut(Qt.Key.Key_Home, self._goto_first)
        self._add_shortcut(Qt.Key.Key_End, self._goto_last)
        self._add_shortcut(Qt.Key.Key_S, self._align_current_frames)
        self._add_shortcut(Qt.Key.Key_R, self._clear_sync)
        self._add_shortcut(Qt.Key.Key_F9, self._toggle_record)
        self._add_shortcut(QKeySequence.StandardKey.Open, self._open_active)

        self._ui_timer.start(UI_TICK_MS)
        self._drift_timer.start(DRIFT_TICK_MS)
        self._load_timer.start(LOAD_TICK_MS)

        if left is not None:
            self.open_video("a", left)
        if right is not None:
            self.open_video("b", right)
        self.statusBar().showMessage("Drop a video into each block to compare")
        # Warm the recorder's ffmpeg probe cache in the background so
        # the first Rec click starts instantly instead of paying ~1s
        # of probe latency (subprocesses never touch the GIL).
        threading.Thread(target=rec.warm_up_probe, daemon=True).start()
        logger.info("main window initialized")

    def _wire_view(self, view: VideoView, view_id: ViewId) -> None:
        """Connect one video block's signals and per-view buttons."""
        view.file_dropped.connect(lambda p, v=view_id: self.open_video(v, Path(p)))
        view.clicked.connect(self._set_active_view)
        view.btn_step_back.clicked.connect(lambda checked=False, v=view_id: self._step_view(v, -1))
        view.btn_step_fwd.clicked.connect(lambda checked=False, v=view_id: self._step_view(v, 1))
        view.btn_play.clicked.connect(lambda checked=False, v=view_id: self._toggle_play_view(v))

    def _add_shortcut(
        self, key: Qt.Key | QKeySequence | QKeySequence.StandardKey, handler: Callable[[], None]
    ) -> None:
        sc = QShortcut(key, self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(handler)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Persist config and release mpv instances."""
        # Timers must die first: the event loop keeps spinning while mpv is
        # terminated below, and a tick touching a dead core raises.
        self._closing = True
        self._ui_timer.stop()
        self._drift_timer.stop()
        self._load_timer.stop()
        self._playing = False  # stop playback
        if self._recorder is not None:
            self._recorder.terminate()  # finalize the mp4 before we die
        self.config.window.width = self.width()
        self.config.window.height = self.height()
        self.config.save()
        for view in (self.view_a, self.view_b):
            with contextlib.suppress(RuntimeError):
                view.player.terminate()  # player may never have been created
        super().closeEvent(event)

    # -- loading ------------------------------------------------------------------

    def open_video(self, view: ViewId, path: Path) -> None:
        """Load a video into view A or B."""
        self._stop_playback()
        # A previous sync offset is meaningless for a new file.
        if self.engine is not None and self.engine.state.is_complete:
            self._clear_sync()
        player = self.view_a.player if view == "a" else self.view_b.player
        try:
            player.load(path)
        except PlayerError as exc:
            QMessageBox.warning(self, "open failed", str(exc))
            return
        self._load_pending[view] = (path, 0)
        self.config.last_dir = str(path.parent)
        logger.info("view %s <- %s", view, path)

    def _on_load_tick(self) -> None:
        """Poll pending loads; finalize range once both views are ready."""
        # Iterate a snapshot and delete finished entries BEFORE any modal
        # dialog runs: QMessageBox spins the event loop, so this slot can
        # re-enter while the dialog is open; a deferred delete would then
        # raise KeyError or drop an entry queued during the dialog.
        for view_id, (path, waited) in list(self._load_pending.items()):
            view = self.view_a if view_id == "a" else self.view_b
            meta = view.player.try_capture_meta()
            if meta is None and waited < LOAD_TIMEOUT_MS:
                self._load_pending[view_id] = (path, waited + LOAD_TICK_MS)
                continue
            del self._load_pending[view_id]
            if meta is not None:
                view.set_loaded_name(meta.path.name)
                self.statusBar().showMessage(f"{view_id.upper()}: {path.name} loaded")
            elif view.player.duration is not None:
                # Duration but no frame rate: a container without fps info
                # cannot be synced (playback itself may still work).
                QMessageBox.warning(
                    self,
                    "open failed",
                    f"No frame rate in this container:\n{path}\n"
                    "(sync comparison needs a readable frame rate)",
                )
            else:
                QMessageBox.warning(self, "open failed", f"Timed out loading:\n{path}")

        # Push per-side durations without waiting for engine.ready: with
        # only one side loaded the other renders as a dead track, and the
        # loaded side already shows its real extent.
        if self.engine is not None:
            durs = (self.view_a.player.duration, self.view_b.player.duration)
            if durs != self._timeline_durs:
                self._timeline_durs = durs
                self.timeline.set_durations(*durs)

    # -- transport ------------------------------------------------------------------

    def _require_engine(self) -> SyncEngine | None:
        if self.engine is None or not self.engine.ready:
            self.statusBar().showMessage("Load both videos first")
            return None
        return self.engine

    @staticmethod
    def _at_stream_end(player: PlayerController) -> bool:
        """True when a player sits at the end of its stream (keep-open EOF)."""
        t, dur = player.time_pos, player.duration
        return t is not None and dur is not None and t >= dur - 1e-6

    def _toggle_play(self) -> None:
        eng = self._require_engine()
        if eng is None:
            return
        pa, pb = self.view_a.player, self.view_b.player
        if self._playing or pa.is_playing or pb.is_playing:
            pa.pause()
            pb.pause()
            self._playing = False
            # mpv pauses asynchronously; re-align once both have settled.
            QTimer.singleShot(200, self._resync_if_needed)
            return
        lo, hi = eng.master_range()
        try:
            if eng.master_time() >= hi - 1e-6:
                eng.seek_master(lo)
        except SyncError:
            pass  # A is mid-load (no time yet): start from wherever it is
        # When synced, snap B onto A's aligned position before starting —
        # but only when meaningfully off. An unconditional seek restarts
        # B's decode pipeline (keyframe backstep plus decode forward) and
        # visibly stalls B while A already plays; sub-frame residuals are
        # converged by the regulate() speed loop instead.
        if eng.state.is_complete:
            eng.resync_if_needed()
        pa.play()
        pb.play()
        self._playing = True
        # No startup resync here: a hard seek during playback restarts B's
        # pipeline and creates the very skew it removes. The startup offset
        # (~40 ms pipeline wake-up asymmetry) is converged by the speed loop.

    def _stop_playback(self) -> None:
        """Pause both views (free-run playback stops; positions stay put)."""
        self._playing = False
        if self.view_a.player.is_playing or self.view_b.player.is_playing:
            self.view_a.player.pause()
            self.view_b.player.pause()
            logger.debug("playback stopped")

    def _resync_if_needed(self) -> None:
        """Snap B onto A's aligned time if off by more than ~one frame."""
        # Runs from a delayed timer after pausing: skip when playback
        # restarted in the meantime — a mid-playback hard seek is exactly
        # what the free-run design avoids.
        if self._closing or self._playing:
            return
        eng = self.engine
        # Without sync points there is no aligned position to restore:
        # leave manually offset views alone (sync-point hunting workflow).
        if eng is None or not eng.ready or not eng.state.is_complete:
            return
        eng.resync_if_needed()

    def _step(self, frames: int) -> None:
        eng = self._require_engine()
        if eng is not None:
            self._stop_playback()
            eng.step(frames)

    def _step_view(self, view: ViewId, frames: int) -> None:
        """Step one view independently (used to find the sync frame)."""
        self._stop_playback()
        player = self.view_a.player if view == "a" else self.view_b.player
        player.step_frames(frames)

    def _toggle_play_view(self, view: ViewId) -> None:
        """Play/pause one view alone, for hunting the sync frame.

        If both views are playing in sync, stop that first so the other view
        stays put while this one is being positioned.
        """
        view_widget = self.view_a if view == "a" else self.view_b
        if view_widget.player.meta is None:
            self.statusBar().showMessage(f"{view.upper()}: load a video first")
            return
        if self._playing:
            self._stop_playback()
        player = view_widget.player
        player.toggle_play()
        if player.is_playing and self._at_stream_end(player):
            player.seek_exact(0.0)
            player.play()

    def _goto_first(self) -> None:
        eng = self._require_engine()
        if eng is not None:
            self._stop_playback()
            lo, _ = eng.master_range()
            eng.seek_master(lo)

    def _goto_last(self) -> None:
        eng = self._require_engine()
        if eng is not None:
            self._stop_playback()
            lo, hi = eng.master_range()
            eng.seek_master(max(lo, hi - 1.0 / eng.reference_fps))

    def _on_scrub_preview(self, t: float) -> None:
        """Coarse seek while dragging; stops playback so they don't fight."""
        eng = self._require_engine()
        if eng is None:
            return
        self._stop_playback()
        eng.seek_master(t, exact=False)

    def _on_seek_committed(self, t: float) -> None:
        """Exact seek on mouse release."""
        eng = self._require_engine()
        if eng is not None:
            self._stop_playback()
            eng.seek_master(t)

    def _on_speed_changed(self, index: int) -> None:
        speed = float(self.speed_combo.itemData(index))
        self.config.speed = speed
        self.view_a.player.set_speed(speed)
        self.view_b.player.set_speed(speed)

    def _cycle_speed(self, direction: int) -> None:
        idx = min(len(SPEED_STEPS) - 1, max(0, self.speed_combo.currentIndex() + direction))
        self.speed_combo.setCurrentIndex(idx)

    # -- sync ------------------------------------------------------------------

    def _set_active_view(self, view: str) -> None:
        self._active_view = "a" if view == "A" else "b"

    def _align_current_frames(self) -> None:
        """One-shot sync: both current frames become the anchor."""
        eng = self._require_engine()
        if eng is None:
            return
        self._stop_playback()
        try:
            offset = eng.sync_here()
        except SyncError as exc:
            logger.warning("align failed: %s", exc)
            self.statusBar().showMessage(f"Cannot sync: {exc}")
            return
        lo, hi = eng.master_range()
        self.timeline.set_anchors(eng.state.point_a, eng.state.point_b)
        eng.seek_master(min(max(eng.master_time(), lo), hi))
        self._set_sync_chip(offset)
        self.statusBar().showMessage(f"Synced: offset = {offset:+.3f}s")

    def _set_sync_chip(self, offset: float | None) -> None:
        """Update the sync state chip (None = not synced)."""
        if offset is None:
            self.sync_chip.setText("● not synced")
            self.sync_chip.setProperty("state", "off")
        else:
            self.sync_chip.setText(f"● {offset:+.3f}s")
            self.sync_chip.setProperty("state", "on")
        # Force QSS re-evaluation after a dynamic property change.
        self.sync_chip.style().unpolish(self.sync_chip)
        self.sync_chip.style().polish(self.sync_chip)

    def _clear_sync(self) -> None:
        if self.engine is None:
            return
        self._stop_playback()
        self.engine.clear_sync()
        self.timeline.set_anchors(None, None)
        self._set_sync_chip(None)
        self.statusBar().showMessage("Sync cleared")

    # -- recording ------------------------------------------------------------------

    def _toggle_record(self) -> None:
        """F9 / Rec button: start recording, or stop when one is running."""
        if self._recorder is not None and self._recorder.recording:
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self) -> None:
        """Probe ffmpeg and launch a capture of the views' screen region."""
        ffmpeg = rec.locate_ffmpeg()
        if ffmpeg is None:
            self.statusBar().showMessage("Recording unavailable: ffmpeg not found")
            return
        support = rec.probe_capabilities(ffmpeg)
        if support is None:
            self.statusBar().showMessage("Recording unavailable: no capture pipeline")
            return
        if self.isMinimized():
            self.statusBar().showMessage("Restore the window before recording")
            return
        region = rec.physical_region(self.splitter)
        screens = rec.screen_physical_rects()
        # Clip invisible slivers a maximized window hangs past the screen.
        region = rec.clamp_region_to_desktop(region, screens)
        primary = QGuiApplication.primaryScreen()
        pg = primary.geometry()
        pdpr = primary.devicePixelRatio()
        prect = (pg.x(), pg.y(), round(pg.width() * pdpr), round(pg.height() * pdpr))
        # ddagrab addresses the primary display only (DDA output 0):
        # the region must sit fully inside the primary screen's physical
        # rect; off-primary needs the gdigrab pipeline, if this ffmpeg
        # build has one at all.
        profile = support.primary
        if profile.capture == "ddagrab" and not rec.region_within(region, prect):
            if support.off_primary is None:
                self.statusBar().showMessage("Move the window to the primary screen to record")
                return
            profile = support.off_primary
        # Reject regions with corners off the virtual desktop (window
        # half off-screen, parked at -32000, ...): ffmpeg cannot capture.
        if not rec.region_fully_on_screens(region, screens):
            self.statusBar().showMessage(
                "Recording region is off-screen; move the window fully onto a screen"
            )
            return
        out = rec.unique_output_path(rec.default_output_dir(), rec.output_timestamp())
        try:
            recorder = self._recorder
            if recorder is None:
                recorder = rec.ScreenRecorder(ffmpeg, profile, self)
                recorder.stateChanged.connect(self._on_rec_state)
                recorder.error.connect(self._on_rec_error)
                recorder.saved.connect(self._on_rec_saved)
                # Assign before start(): the "recording" state signal
                # fires synchronously inside start() and its slot reads
                # self._recorder for the output path.
                self._recorder = recorder
            recorder.start(region, out, profile)
        except (rec.RecorderError, OSError, RuntimeError) as exc:
            QMessageBox.warning(self, "record failed", str(exc))
            return
        self._rec_secs = -1
        logger.info("recording region %s -> %s", region.as_ffmpeg_geom(), out)

    def _stop_recording(self) -> None:
        """Graceful stop; the recorder's own watchdog force-kills hangs."""
        recorder = self._recorder
        if recorder is not None:
            recorder.stop()

    def _on_rec_state(self, state: str) -> None:
        if state == "recording":
            self.btn_rec.setText("⏺ 0:00")
            self._set_rec_button_active(True)
            if self._recorder is not None and self._recorder.output is not None:
                self.statusBar().showMessage(f"Recording → {self._recorder.output.name}")
        elif state == "stopping":
            self.btn_rec.setText("⏹ finishing…")
        else:  # finished / failed
            self.btn_rec.setText("⏺ Rec")
            self._set_rec_button_active(False)

    def _set_rec_button_active(self, active: bool) -> None:
        self.btn_rec.setProperty("rec", "on" if active else "off")
        self.btn_rec.style().unpolish(self.btn_rec)
        self.btn_rec.style().polish(self.btn_rec)

    def _on_rec_saved(self, path: str) -> None:
        logger.info("recording saved: %s", path)
        if self._closing:
            return  # no Explorer popping up while the window dies
        self.statusBar().showMessage(f"Saved clip: {path}")
        # Reveal the file in Explorer (fire-and-forget, absolute path:
        # never resolve "explorer" through CWD-relative search order).
        with contextlib.suppress(OSError):
            subprocess.Popen([str(Path(os.environ["WINDIR"]) / "explorer.exe"), "/select,", path])

    def _on_rec_error(self, message: str) -> None:
        logger.warning("recording failed: %s", message)
        if not self._closing:
            QMessageBox.warning(self, "recording failed", message)

    # -- file dialog ------------------------------------------------------------------

    def _open_dialog(self, view: ViewId) -> None:
        start = self.config.last_dir or str(Path.home())
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXTS))
        path_str, _ = QFileDialog.getOpenFileName(
            self, f"Open video {view.upper()}", start, f"Video files ({exts});;All files (*)"
        )
        if path_str:
            self.open_video(view, Path(path_str))

    def _open_active(self) -> None:
        self._open_dialog(self._active_view)

    # -- timers ------------------------------------------------------------------

    def _on_ui_tick(self) -> None:
        # Recording elapsed time on the Rec button (once per second) runs
        # before the engine guard: recording works without loaded videos.
        # Only while truly RECORDING: in STOPPING the "finishing…" label
        # must not be overwritten by the timer.
        if self._recorder is not None and self._recorder.state is rec.RecorderState.RECORDING:
            secs = int(self._recorder.elapsed_seconds())
            if secs != self._rec_secs:
                self._rec_secs = secs
                self.btn_rec.setText(f"⏺ {secs // 60}:{secs % 60:02d}")
        if self.engine is None:
            return
        try:
            master = self.engine.master_time()
        except SyncError:
            return  # view A not loaded yet
        # Follow each player's real state, not the global sync flag: a
        # solo-playing view must animate its own track.
        a_playing = self.view_a.player.is_playing
        if a_playing:
            # playback-time is clock-interpolated: smoother than time_pos.
            pt = self.view_a.player.playback_time
            if pt is not None:
                master = pt
        shown = self._smooth_playhead(master, a_playing)
        # Each knob shows its own video's actual local time (so solo
        # playback moves only that track); the mapped position is a
        # fallback while B is mid-load and reports no time yet.
        t_b = self.view_b.player.playback_time
        if t_b is None:
            t_b = self.view_b.player.time_pos
        if t_b is None:
            t_b = self.engine.view_time("b", shown)
        dur_b = self.view_b.player.duration
        if dur_b is not None:
            t_b = min(t_b, dur_b)
        self.timeline.set_playheads(shown, t_b)
        self._update_view_time(self.view_a)
        self._update_view_time(self.view_b)
        self.view_a.set_play_text(self.view_a.player.is_playing)
        self.view_b.set_play_text(self.view_b.player.is_playing)
        drift = self.engine.drift()
        drift_ms = f"{drift * 1000:+.1f}ms" if drift is not None else "n/a"
        self._status_live.setText(
            f"A {master:8.3f}s | offset {self.engine.offset:+.3f}s | drift {drift_ms} "
            f"| {self.engine.reference_fps:g}fps"
        )
        self.btn_play.setText("Pause" if self._playing else "Play")

    def _smooth_playhead(self, raw: float, playing: bool) -> float:
        """Interpolate between mpv frame updates so the playhead glides."""
        now = time.monotonic()
        if not playing or raw != self._playhead_raw:
            self._playhead_raw = raw
            self._playhead_anchor = raw
            self._playhead_mono = now
            return raw
        est = self._playhead_anchor + (now - self._playhead_mono) * self.config.speed
        # Clamp to A's own extent: the A track spans the full video, so a
        # solo-playing A past the sync overlap must keep moving its knob
        # (clamping to master_range would freeze it mid-track while the
        # status bar keeps counting).
        dur_a = self.view_a.player.duration
        if dur_a is not None:
            est = min(max(est, 0.0), dur_a)
        return est

    @staticmethod
    def _update_view_time(view: VideoView) -> None:
        t = view.player.time_pos
        view.set_time_text(f"{t:.3f}s" if t is not None else "--")

    def _on_drift_tick(self) -> None:
        """Continuous sync regulation during free-run playback.

        Small drift is absorbed by nudging B's speed (up to +/-10%); a hard
        seek fires only beyond a few frames. Community-validated approach
        (Syncplay / mpv issue #13905 use the same scheme).
        """
        eng = self.engine
        if eng is None or not eng.ready or not self._playing:
            return
        if not eng.state.is_complete:
            # An offset of 0 is not a sync: without an anchor, nudging B's
            # speed would drag an intentionally offset view back onto A.
            return
        eng.regulate(self.config.speed)
