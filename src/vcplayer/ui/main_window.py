"""Main window: N dynamic video blocks + shared transport + timeline + sync.

Each video block (VideoView) has its own frame-step and sync controls, which
is required for the sync-point workflow: step each view independently to the
same physical moment, then mark all sync points at once.

Views are added (＋ / V) and removed (－) dynamically, 2 minimum, 4 maximum.
View A is the master timeline; every other view gets its own SyncEngine
against A, so offsets and drift are tracked per follower pair.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..core import recorder as rec
from ..core.player import PlayerController, PlayerError
from ..core.sync import SyncEngine, SyncError, ViewId, intersect_master_ranges
from . import layout as pane_layout
from .timeline import TimelineWidget
from .video_view import VIDEO_EXTS, VideoView
from .view_host import ViewHost

logger = logging.getLogger(__name__)

SPEED_STEPS = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0]
UI_TICK_MS = 15  # ~66 Hz so the playhead tracks high-fps video smoothly
DRIFT_TICK_MS = 100
LOAD_TICK_MS = 100
LOAD_TIMEOUT_MS = 10_000

MIN_VIEWS = 2
MAX_VIEWS = 4
ALL_VIEW_IDS: tuple[ViewId, ...] = ("a", "b", "c", "d")


class MainWindow(QMainWindow):
    """Top-level window assembling views, transport, timeline and sync logic."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("video-compare-player")
        self.resize(config.window.width, config.window.height)

        self.views: dict[ViewId, VideoView] = {"a": VideoView("A"), "b": VideoView("B")}
        self.view_ids: list[ViewId] = ["a", "b"]
        self.engines: dict[ViewId, SyncEngine] = {}
        self._initialized = False
        # Row-list pane arrangement; ViewHost only moves widgets inside its
        # own grid (never reparents: the mpv surfaces are native windows).
        self._layout: pane_layout.Layout = pane_layout.default_layout(self.view_ids)
        # False while the arrangement is still the untouched default; a
        # user drag/preset flips it so add/remove preserves the layout.
        self._layout_customized = False
        self._drag_source: str | None = None
        self._drag_changed = False
        self._shortcuts: list[QShortcut] = []

        self.view_host = ViewHost()
        for view in self.views.values():
            self.view_host.add_view(view)
        self.view_host.apply_layout(self._layout)

        self.timeline = TimelineWidget()

        self.btn_first = QPushButton("|<")
        self.btn_step_back = QPushButton("<")
        self.btn_play = QPushButton("Play")
        self.btn_play.setObjectName("playButton")
        self.btn_step_fwd = QPushButton(">")
        self.btn_last = QPushButton(">|")
        # Buttons must not keep focus: a focused button is re-activated by
        # Space/Enter, double-triggering shortcuts (e.g. Space toggles play
        # via QShortcut AND clicks the focused Play button). NoFocus keeps
        # mouse interaction but drops all buttons from key delivery.
        for btn in (
            self.btn_first,
            self.btn_step_back,
            self.btn_play,
            self.btn_step_fwd,
            self.btn_last,
        ):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_combo = QComboBox()
        for s in SPEED_STEPS:
            self.speed_combo.addItem(f"{s:g}x", s)
        self.speed_combo.setCurrentIndex(
            SPEED_STEPS.index(config.speed) if config.speed in SPEED_STEPS else 3
        )
        self._open_btns: dict[ViewId, QPushButton] = {}
        for vid in ALL_VIEW_IDS:
            btn = QPushButton(f"Open {vid.upper()}")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._open_btns[vid] = btn
        self.btn_add = QPushButton("＋")
        self.btn_add.setObjectName("addViewButton")
        self.btn_add.setToolTip("Add another comparison view. (V)")
        self.btn_remove = QPushButton("－")
        self.btn_remove.setObjectName("removeViewButton")
        self.btn_remove.setToolTip("Remove the last view.")
        self.btn_align = QPushButton("⇄ Align current frames")
        self.btn_align.setObjectName("alignButton")
        self.btn_align.setToolTip(
            "Step each video to the same physical moment (e.g. stopwatch tick),\n"
            "then click this: all current frames become the sync anchors. (S)"
        )
        self.sync_chip = QLabel("● not synced")
        self.sync_chip.setObjectName("syncChip")
        self.sync_chip.setProperty("state", "off")
        self.btn_sync_clear = QPushButton("Clear sync")
        self.btn_sync_clear.setObjectName("syncButton")
        self.btn_rec = QPushButton("⏺ Rec")
        self.btn_rec.setObjectName("recButton")
        self.btn_rec.setToolTip(
            "Record the video views to an mp4 clip. (F9)\n"
            "Do not move or resize the window while recording."
        )
        self.btn_crosshair = QPushButton("✛")
        self.btn_crosshair.setObjectName("crosshairButton")
        self.btn_crosshair.setCheckable(True)
        self.btn_crosshair.setToolTip(
            "Show a crosshair at the center of every view,\n"
            "a fixed reference for comparing motion amplitude. (C)"
        )
        # Same NoFocus treatment for the remaining shortcut-bearing buttons
        # (S / R / F9 / C / V keys would otherwise re-trigger them).
        for btn in (
            self.btn_add,
            self.btn_remove,
            self.btn_align,
            self.btn_sync_clear,
            self.btn_rec,
            self.btn_crosshair,
        ):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        transport = QHBoxLayout()
        transport.addStretch(1)
        for vid in ALL_VIEW_IDS:
            transport.addWidget(self._open_btns[vid])
        transport.addWidget(self.btn_add)
        transport.addWidget(self.btn_remove)
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
        transport.addWidget(self.btn_crosshair)
        transport.addStretch(1)

        central = QWidget()
        central.setObjectName("centralContainer")
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)
        vbox.addWidget(self.view_host, stretch=1)
        vbox.addWidget(self.timeline)
        vbox.addLayout(transport)
        self.setCentralWidget(central)

        self._active_view: ViewId = "a"
        self._load_pending: dict[ViewId, tuple[Path, int]] = {}
        # Playhead smoothing state: interpolate between mpv frame updates.
        self._playhead_raw = 0.0
        self._playhead_anchor = 0.0
        self._playhead_mono = 0.0
        self._playing = False
        self._closing = False
        self._drift_tick_count = 0
        # Live readout lives in a permanent widget: showMessage() stays free
        # for event messages ("Synced: ..."), which would otherwise be
        # clobbered by the 15 ms UI tick.
        self._status_live = QLabel("")
        self.statusBar().addPermanentWidget(self._status_live)
        self._timeline_durs: dict[ViewId, float | None] | None = None
        # Screen recorder session (created on first F9/Rec click).
        self._recorder: rec.ScreenRecorder | None = None
        self._rec_secs = -1  # last elapsed second shown on the button

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._on_ui_tick)
        self._drift_timer = QTimer(self)
        self._drift_timer.timeout.connect(self._on_drift_tick)
        self._load_timer = QTimer(self)
        self._load_timer.timeout.connect(self._on_load_tick)

        self._refresh_view_buttons()

    # -- lifecycle --------------------------------------------------------------

    def initialize(self, videos: Sequence[Path]) -> None:
        """Create the sync engines and wire signals; call after the window is shown."""
        if self._initialized or self._closing:
            return  # single-shot entry; never double-wire shortcuts/signals
        try:
            self.views["a"].ensure_player()
            self.views["b"].ensure_player()
            self.engines["b"] = SyncEngine(self.views["a"].player, self.views["b"].player)
        except (RuntimeError, PlayerError) as exc:
            QMessageBox.critical(self, "mpv error", str(exc))
            self.close()  # never leave a dead, unresponsive window on screen
            return

        self._wire_view(self.views["a"], "a")
        self._wire_view(self.views["b"], "b")
        self._connect_gestures(self.views["a"])
        self._connect_gestures(self.views["b"])
        self.timeline.scrub_preview.connect(self._on_scrub_preview)
        self.timeline.seek_committed.connect(self._on_seek_committed)

        for vid in ALL_VIEW_IDS:
            self._open_btns[vid].clicked.connect(lambda _=False, v=vid: self._open_dialog(v))
        self.btn_add.clicked.connect(lambda: self._add_view())
        self.btn_remove.clicked.connect(self._remove_last_view)
        self.btn_first.clicked.connect(self._goto_first)
        self.btn_step_back.clicked.connect(lambda: self._step(-1))
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_step_fwd.clicked.connect(lambda: self._step(1))
        self.btn_last.clicked.connect(self._goto_last)
        self.speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self.btn_align.clicked.connect(self._align_current_frames)
        self.btn_sync_clear.clicked.connect(self._clear_sync)
        self.btn_rec.clicked.connect(self._toggle_record)
        self.btn_crosshair.toggled.connect(self._on_crosshair_toggled)

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
        self._add_shortcut(Qt.Key.Key_C, self._toggle_crosshair)
        self._add_shortcut(Qt.Key.Key_V, self._add_view)
        self._add_shortcut(QKeySequence.StandardKey.Open, self._open_active)

        self._ui_timer.start(UI_TICK_MS)
        self._drift_timer.start(DRIFT_TICK_MS)
        self._load_timer.start(LOAD_TICK_MS)

        # Apply persisted playback speed through the combo's sanitized
        # value: mpv defaults to 1.0x regardless of the combo box, and a
        # hand-edited config could hold a value outside SPEED_STEPS (combo
        # falls back to 1.0x) — routing through itemData keeps displayed,
        # applied and persisted speed identical by construction.
        speed = float(self.speed_combo.itemData(self.speed_combo.currentIndex()))
        self.config.speed = speed
        for view in self.views.values():
            view.player.set_speed(speed)

        self._initialized = True
        # Players exist now: apply the persisted crosshair state (setChecked
        # only fires toggled on an actual change).
        self.btn_crosshair.setChecked(self.config.crosshair)

        # Views beyond the first two come from the CLI (2-4 videos).
        for path in videos[2:]:
            self._add_view(load=path)
        if len(videos) > 0 and videos[0] is not None:
            self.open_video("a", videos[0])
        if len(videos) > 1 and videos[1] is not None:
            self.open_video("b", videos[1])
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
        view.layout_drag_started.connect(self._on_layout_drag_started)
        view.layout_drag_finished.connect(self._on_layout_drag_finished)
        view.view_dropped_on.connect(self._on_view_dropped_on)
        view.drop_zone_changed.connect(self._on_drop_zone_changed)
        view.context_menu_requested.connect(self._on_view_context_menu)
        view.btn_step_back.clicked.connect(lambda checked=False, v=view_id: self._step_view(v, -1))
        view.btn_step_fwd.clicked.connect(lambda checked=False, v=view_id: self._step_view(v, 1))
        view.btn_play.clicked.connect(lambda checked=False, v=view_id: self._toggle_play_view(v))

    def _connect_gestures(self, view: VideoView) -> None:
        """Steer all other surfaces when one view gestures (non-Ctrl)."""
        view.surface.paired_zoom.connect(
            lambda direction, v=view: self._broadcast_zoom(v, direction)
        )
        view.surface.paired_pan.connect(lambda dx, dy, v=view: self._broadcast_pan(v, dx, dy))
        view.surface.paired_reset.connect(lambda v=view: self._broadcast_reset(v))

    def _broadcast_zoom(self, source: VideoView, direction: int) -> None:
        for view in self.views.values():
            if view is not source:
                view.surface.apply_paired_zoom(direction)

    def _broadcast_pan(self, source: VideoView, dx_px: float, dy_px: float) -> None:
        for view in self.views.values():
            if view is not source:
                view.surface.apply_paired_pan(dx_px, dy_px)

    def _broadcast_reset(self, source: VideoView) -> None:
        for view in self.views.values():
            if view is not source:
                view.surface.apply_paired_reset()

    def _add_shortcut(
        self, key: Qt.Key | QKeySequence | QKeySequence.StandardKey, handler: Callable[[], None]
    ) -> None:
        sc = QShortcut(key, self)
        sc.setContext(Qt.ShortcutContext.WindowShortcut)
        sc.activated.connect(handler)
        self._shortcuts.append(sc)

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
        for view in self.views.values():
            with contextlib.suppress(RuntimeError):
                view.player.terminate()  # player may never have been created
        super().closeEvent(event)

    # -- dynamic views --------------------------------------------------------

    def _recording(self) -> bool:
        return self._recorder is not None and self._recorder.recording

    def _add_view(self, load: Path | None = None) -> None:
        """Append a new view (up to MAX_VIEWS); optionally load a video."""
        if not self._initialized:
            return
        if self._recording():
            self.statusBar().showMessage("Stop recording before changing views")
            return
        if len(self.view_ids) >= MAX_VIEWS:
            self.statusBar().showMessage(f"Maximum {MAX_VIEWS} views")
            return
        vid: ViewId = ALL_VIEW_IDS[len(self.view_ids)]
        view = VideoView(vid.upper())
        # Parent the widget before creating its player: ensure_player() hands
        # the pane's native window to mpv, and reparenting afterwards could
        # recreate that handle. The view is registered up front and rolled
        # back on failure — the rollback runs before any modal dialog so the
        # nested event loop never sees the half-registered state.
        prior_ids = list(self.view_ids)
        old_layout = [list(row) for row in self._layout]
        self.views[vid] = view
        self.view_host.add_view(view)
        self._layout = pane_layout.add_view(
            self._layout, vid, prior_ids, customized=self._layout_customized
        )
        # Minimum sizes must be updated before the grid re-activates: doing
        # it afterwards leaves the top-level minimum stuck at the previous,
        # possibly larger, layout until the next resize event.
        self._apply_view_size_policy()
        self.view_host.apply_layout(self._layout)
        view.set_drag_enabled(not self._recording())
        try:
            view.ensure_player()
            self.engines[vid] = SyncEngine(self.views["a"].player, view.player)
            self._wire_view(view, vid)
            self._connect_gestures(view)
            view.player.set_speed(self.config.speed)
            if self.btn_crosshair.isChecked():
                view.player.set_crosshair(True)
        except (RuntimeError, PlayerError, SystemError) as exc:
            logger.warning("view %s failed to start: %s", vid, exc)
            self._destroy_view(vid)
            self._layout = old_layout
            self._apply_view_size_policy()
            self.view_host.apply_layout(self._layout)
            QMessageBox.warning(self, "mpv error", str(exc))
            return
        self.view_ids.append(vid)
        self._refresh_view_buttons()
        self.timeline.set_tracks(self.view_ids)
        self._timeline_durs = None  # force a refresh for the new track
        self._refresh_sync_ui()
        logger.info("view %s added (%d views)", vid, len(self.view_ids))
        if load is not None:
            self.open_video(vid, load)
        else:
            self.statusBar().showMessage(f"View {vid.upper()} added — drop or open a video")

    def _remove_last_view(self) -> None:
        """Tear down the most recently added view (A/B stay)."""
        if not self._initialized or len(self.view_ids) <= MIN_VIEWS:
            return
        if self._recording():
            self.statusBar().showMessage("Stop recording before changing views")
            return
        self._stop_playback()
        prior_ids = list(self.view_ids)
        vid = self.view_ids[-1]
        self._layout = pane_layout.remove_view(
            self._layout, vid, prior_ids, customized=self._layout_customized
        )
        self._apply_view_size_policy()
        self._destroy_view(vid)
        self.view_host.apply_layout(self._layout)
        if self._active_view == vid:
            self._active_view = "a"
        self._refresh_view_buttons()
        self.timeline.set_tracks(self.view_ids)
        self._timeline_durs = None  # force a refresh without the dropped view
        self._refresh_sync_ui()
        logger.info("view %s removed (%d views)", vid, len(self.view_ids))
        self.statusBar().showMessage(f"View {vid.upper()} removed")

    def _apply_view_size_policy(self) -> None:
        """Shrink per-view minimums with the widest row / row count.

        MpvSurface enforces 400x260 by default; four of those (~1630
        logical px) exceed the usable width on scaled 1080p screens and
        force the window off-screen (which also blocks recording). A 2x2
        grid only needs the width of a two-pane row, so the limit follows
        the layout rather than the view count.
        """
        widest = max((len(row) for row in self._layout), default=1)
        rows = max(1, len(self._layout))
        min_w = {1: 400, 2: 400, 3: 300, 4: 240}.get(widest, 240)
        min_h = 260 if rows == 1 else 150
        for view in self.views.values():
            view.surface.setMinimumSize(min_w, min_h)

    def _destroy_view(self, vid: ViewId) -> None:
        """Remove a view from state and life (player released)."""
        self.view_ids = [v for v in self.view_ids if v != vid]
        view = self.views.pop(vid, None)
        self.engines.pop(vid, None)
        self._load_pending.pop(vid, None)
        if view is not None:
            with contextlib.suppress(RuntimeError):
                view.player.terminate()
            self.view_host.remove_view(view)
            view.deleteLater()

    # -- pane arrangement ------------------------------------------------------

    def _set_views_drag_enabled(self, enabled: bool) -> None:
        for view in self.views.values():
            view.set_drag_enabled(enabled)

    def _on_layout_drag_started(self, source: str) -> None:
        # The drag runs its own nested event loop: shortcuts would still
        # fire (Space, arrows, R, ...) while the pointer is captured.
        self._drag_source = source
        self._drag_changed = False
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        self.statusBar().showMessage(
            f"Moving view {source.upper()} — drop on an edge to insert, "
            "center to swap · Esc cancels"
        )

    def _on_layout_drag_finished(self) -> None:
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        for view in self.views.values():
            view.set_drop_zone(None)
            view.set_drag_source(False)
        self.view_host.clear_drop_state()
        if not self._drag_changed:
            self.statusBar().showMessage("Layout unchanged")
        self._drag_source = None

    def _on_drop_zone_changed(self, target: str, zone: str) -> None:
        """Live status hint for the pending drop (target/zone under the pointer)."""
        source = self._drag_source
        if source is None or not zone or source == target:
            return
        if zone == pane_layout.ZONE_CENTER:
            message = f"Release to swap {source.upper()} with {target.upper()}"
        else:
            where = {
                pane_layout.ZONE_LEFT: "left of",
                pane_layout.ZONE_RIGHT: "right of",
                pane_layout.ZONE_TOP: "above",
                pane_layout.ZONE_BOTTOM: "below",
            }.get(zone, "beside")
            message = f"Release to insert {source.upper()} {where} {target.upper()}"
        self.statusBar().showMessage(message)

    def _on_view_dropped_on(self, source: str, target: str, zone: str) -> None:
        if self._recording():
            self.statusBar().showMessage("Stop recording before rearranging views")
            return
        if source not in self.views or target not in self.views or source == target:
            return
        new_layout = pane_layout.apply_zone(self._layout, source, target, zone)
        if new_layout == self._layout:
            return
        if not self._set_layout(new_layout):
            return
        self._drag_changed = True
        if zone == pane_layout.ZONE_CENTER:
            message = f"View {source.upper()} swapped with {target.upper()}"
        else:
            where = {
                pane_layout.ZONE_LEFT: "left of",
                pane_layout.ZONE_RIGHT: "right of",
                pane_layout.ZONE_TOP: "above",
                pane_layout.ZONE_BOTTOM: "below",
            }.get(zone, "beside")
            message = f"View {source.upper()} inserted {where} {target.upper()}"
        self.statusBar().showMessage(message)

    def _set_layout(self, new_layout: pane_layout.Layout, *, customized: bool = True) -> bool:
        """Apply a validated arrangement; False when nothing changed."""
        if self._recording():
            self.statusBar().showMessage("Stop recording before rearranging views")
            return False
        if not pane_layout.is_valid(new_layout, self.view_ids):
            return False
        candidate = [list(row) for row in new_layout]
        if candidate == self._layout:
            return False
        self._layout = candidate
        self._layout_customized = customized
        # Update minimums first: applying the layout first would leave the
        # window minimum stuck at the previous (possibly larger) layout.
        self._apply_view_size_policy()
        self.view_host.apply_layout(self._layout)
        return True

    def _reset_layout(self) -> None:
        if self._recording():
            self.statusBar().showMessage("Stop recording before rearranging views")
            return
        default = pane_layout.default_layout(self.view_ids)
        if not self._set_layout(default, customized=False):
            self._layout_customized = False
            self.statusBar().showMessage("Layout reset")

    def _on_view_context_menu(self, _view_id: str, global_pos: QPoint) -> None:
        menu = QMenu(self)
        reset = menu.addAction("Reset layout", self._reset_layout)
        presets = pane_layout.presets(self.view_ids)
        if presets:
            menu.addSeparator()
            for label, preset in presets:

                def apply_preset(_checked: bool = False, p: pane_layout.Layout = preset) -> None:
                    self._set_layout(p)

                action = menu.addAction(label, apply_preset)
                if self._recording():
                    action.setEnabled(False)
        if self._recording():
            reset.setEnabled(False)
        menu.exec(global_pos)
        menu.deleteLater()

    def _refresh_view_buttons(self) -> None:
        """Show Open buttons for existing views; gate ＋/－ by the limits."""
        for vid, btn in self._open_btns.items():
            btn.setVisible(vid in self.views)
        self.btn_add.setEnabled(len(self.view_ids) < MAX_VIEWS)
        self.btn_remove.setEnabled(len(self.view_ids) > MIN_VIEWS)

    # -- loading ------------------------------------------------------------------

    def open_video(self, view: ViewId, path: Path) -> None:
        """Load a video into one of the views."""
        if view not in self.views:
            return
        self._stop_playback()
        # Sync offsets involving a reloaded view are meaningless for the
        # new file (a reload of A breaks every pair, a follower only its own).
        self._invalidate_sync(view)
        try:
            self.views[view].player.load(path)
        except PlayerError as exc:
            QMessageBox.warning(self, "open failed", str(exc))
            return
        self._load_pending[view] = (path, 0)
        self.config.last_dir = str(path.parent)
        logger.info("view %s <- %s", view, path)

    def _on_load_tick(self) -> None:
        """Poll pending loads; finalize range once both views are ready."""
        if not self._initialized:
            return
        # Iterate a snapshot and delete finished entries BEFORE any modal
        # dialog runs: QMessageBox spins the event loop, so this slot can
        # re-enter while the dialog is open; a deferred delete would then
        # raise KeyError or drop an entry queued during the dialog.
        for view_id, (path, waited) in list(self._load_pending.items()):
            view = self.views.get(view_id)
            if view is None:  # the view was removed while loading
                self._load_pending.pop(view_id, None)
                continue
            meta = view.player.try_capture_meta()
            if meta is None and waited < LOAD_TIMEOUT_MS:
                self._load_pending[view_id] = (path, waited + LOAD_TICK_MS)
                continue
            self._load_pending.pop(view_id, None)
            if meta is not None:
                view.set_loaded_name(meta.path.name)
                self._on_view_loaded(view_id, path)
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

        # Push per-view durations without waiting for engine readiness:
        # with only one side loaded the other renders as a dead track, and
        # the loaded side already shows its real extent.
        durs = {vid: self.views[vid].player.duration for vid in self.view_ids}
        if durs != self._timeline_durs:
            self._timeline_durs = durs
            self.timeline.set_durations(durs)

    def _on_view_loaded(self, view_id: ViewId, path: Path) -> None:
        """Loaded-view bookkeeping: join a running synced playback if able."""
        if self._playing and view_id != "a":
            eng = self.engines.get(view_id)
            if eng is not None and eng.ready and eng.state.is_complete:
                a = self.views["a"].player
                master = a.playback_time
                if master is None:
                    master = a.time_pos
                if master is not None:
                    eng.seek_follower(master)
                    eng.reset_runtime()
                    self.views[view_id].player.play()
                    logger.info("view %s loaded and joined playback", view_id)
                    self.statusBar().showMessage(
                        f"{view_id.upper()}: {path.name} loaded, joined playback"
                    )
                    return
                self.statusBar().showMessage(
                    f"{view_id.upper()}: {path.name} loaded (paused) — press S to align"
                )
                logger.info("view %s loaded (not synced; stays paused)", view_id)
                return
        self.statusBar().showMessage(f"{view_id.upper()}: {path.name} loaded")

    # -- transport ------------------------------------------------------------------

    def _any_engine_ready(self) -> bool:
        return any(eng.ready for eng in self.engines.values())

    def _require_ready(self) -> bool:
        if not self._any_engine_ready():
            self.statusBar().showMessage("Load at least two videos first")
            return False
        return True

    def _master_range(self) -> tuple[float, float] | None:
        """Master interval where every ready pair has frames."""
        return intersect_master_ranges(self.engines.values())

    def _reference_fps(self) -> float:
        for eng in self.engines.values():
            if eng.ready:
                return eng.reference_fps
        fps = self.views["a"].player.fps
        return fps if fps and fps > 0 else 25.0

    def _seek_master_all(self, master: float, *, exact: bool = True) -> None:
        """Seek A once and every ready follower to one master position.

        The target is clamped to the *intersection* of all ready ranges
        before any player moves: clamping per engine would let the last
        engine drag A away from the followers already seeked (the
        intersection is always inside A's own range, so seeking A directly
        with the clamped value is safe).
        """
        rng = self._master_range()
        if rng is None:
            if self._any_engine_ready():
                self.statusBar().showMessage("No common range between loaded views")
            return
        master = min(max(master, rng[0]), rng[1])
        a = self.views["a"].player
        if exact:
            a.seek_exact(master)
        else:
            a.seek_fast(master)
        for eng in self.engines.values():
            if eng.ready:
                eng.seek_follower(master, exact=exact)
                # A hard seek invalidates the EWMA the loop was built on.
                eng.reset_runtime()

    @staticmethod
    def _at_stream_end(player: PlayerController) -> bool:
        """True when a player sits at the end of its stream (keep-open EOF)."""
        t, dur = player.time_pos, player.duration
        return t is not None and dur is not None and t >= dur - 1e-6

    def _toggle_play(self) -> None:
        if not self._require_ready():
            return
        views = [self.views[vid] for vid in self.view_ids]
        if self._playing or any(v.player.is_playing for v in views):
            for v in views:
                v.player.pause()
            self._playing = False
            logger.info("playback paused")
            # mpv pauses asynchronously; re-align once they have settled.
            QTimer.singleShot(200, self._resync_if_needed)
            return
        rng = self._master_range()
        master = self.views["a"].player.time_pos
        if rng is not None and master is not None and master >= rng[1] - 1e-6:
            self._seek_master_all(rng[0])  # all views at EOF: restart together
        # When synced, snap followers onto A's aligned position before
        # starting — but only when meaningfully off. An unconditional seek
        # restarts a decode pipeline (keyframe backstep plus decode forward)
        # and visibly stalls that view while A already plays; sub-frame
        # residuals are converged by the regulate() speed loop instead.
        for eng in self.engines.values():
            if eng.ready and eng.state.is_complete:
                eng.resync_if_needed()
        # Never start a view that is still loading: play() would unpause it
        # and it would begin from 0 once the file demuxes, unsynced. It
        # joins automatically when the load completes (see _on_load_tick).
        for v in views:
            if v.player.meta is not None:
                v.player.play()
        self._playing = True
        logger.info("playback started, speed=%.2f", self.config.speed)
        # No startup resync here: a hard seek during playback restarts the
        # pipelines and creates the very skew it removes. The startup offset
        # (~40 ms pipeline wake-up asymmetry) is converged by the speed loop.

    def _stop_playback(self) -> None:
        """Pause all views (free-run playback stops; positions stay put)."""
        self._playing = False
        if any(self.views[vid].player.is_playing for vid in self.view_ids):
            for vid in self.view_ids:
                self.views[vid].player.pause()
            logger.debug("playback stopped")

    def _resync_if_needed(self) -> None:
        """Snap followers onto A's aligned time if off by more than ~one frame."""
        # Runs from a delayed timer after pausing: skip when playback
        # restarted in the meantime — a mid-playback hard seek is exactly
        # what the free-run design avoids.
        if self._closing or self._playing:
            return
        # Without sync points there is no aligned position to restore:
        # leave manually offset views alone (sync-point hunting workflow).
        if not self._any_engine_ready():
            return
        for eng in self.engines.values():
            if eng.ready and eng.state.is_complete:
                eng.resync_if_needed()

    def _step(self, frames: int) -> None:
        if not self._require_ready():
            return
        self._stop_playback()
        master = self.views["a"].player.time_pos
        if master is None:
            return
        master += frames / self._reference_fps()
        rng = self._master_range()
        if rng is not None:
            master = min(max(master, rng[0]), rng[1])
        self._seek_master_all(master)

    def _step_view(self, view: ViewId, frames: int) -> None:
        """Step one view independently (used to find the sync frame)."""
        if view not in self.views:
            return
        self._stop_playback()
        self.views[view].player.step_frames(frames)

    def _toggle_play_view(self, view: ViewId) -> None:
        """Play/pause one view alone, for hunting the sync frame.

        If views are playing in sync, stop that first so the other views
        stay put while this one is being positioned.
        """
        if view not in self.views:
            return
        player = self.views[view].player
        if player.meta is None:
            self.statusBar().showMessage(f"{view.upper()}: load a video first")
            return
        if self._playing:
            self._stop_playback()
        player.toggle_play()
        if player.is_playing and self._at_stream_end(player):
            player.seek_exact(0.0)
            player.play()

    def _goto_first(self) -> None:
        if not self._require_ready():
            return
        self._stop_playback()
        rng = self._master_range()
        if rng is not None:
            self._seek_master_all(rng[0])

    def _goto_last(self) -> None:
        if not self._require_ready():
            return
        self._stop_playback()
        rng = self._master_range()
        if rng is not None:
            self._seek_master_all(max(rng[0], rng[1] - 1.0 / self._reference_fps()))

    def _on_scrub_preview(self, t: float) -> None:
        """Coarse seek while dragging; stops playback so they don't fight."""
        if not self._require_ready():
            return
        self._stop_playback()
        self._seek_master_all(t, exact=False)

    def _on_seek_committed(self, t: float) -> None:
        """Exact seek on mouse release."""
        if not self._require_ready():
            return
        self._stop_playback()
        self._seek_master_all(t)

    def _on_speed_changed(self, index: int) -> None:
        speed = float(self.speed_combo.itemData(index))
        self.config.speed = speed
        for view in self.views.values():
            view.player.set_speed(speed)

    def _cycle_speed(self, direction: int) -> None:
        idx = min(len(SPEED_STEPS) - 1, max(0, self.speed_combo.currentIndex() + direction))
        self.speed_combo.setCurrentIndex(idx)

    # -- sync ------------------------------------------------------------------

    def _set_active_view(self, view: str) -> None:
        vid = view.lower()
        if vid in self.views:
            self._active_view = vid  # type: ignore[assignment]

    def _current_anchors(self) -> dict[ViewId, float | None]:
        """Anchor per view, rebuilt from the engines' sync states.

        The A-side anchor is taken from the first complete pair (they are
        normally identical; a deterministic pick beats last-wins).
        """
        anchors: dict[ViewId, float | None] = {vid: None for vid in self.view_ids}
        for vid, eng in self.engines.items():
            if eng.state.is_complete:
                if anchors["a"] is None:
                    anchors["a"] = eng.state.point_a
                anchors[vid] = eng.state.point_b
        return anchors

    def _current_offsets(self) -> dict[ViewId, float]:
        """Offsets of every synced follower pair (empty when unsynced)."""
        return {vid: eng.offset for vid, eng in self.engines.items() if eng.state.is_complete}

    def _invalidate_sync(self, view: ViewId) -> None:
        """Drop sync anchors that involve a (re)loaded view."""
        affected = list(self.engines) if view == "a" else [view]
        for vid in affected:
            eng = self.engines.get(vid)
            if eng is not None and eng.state.is_complete:
                eng.clear_sync()
            follower = self.views.get(vid)
            if follower is not None:
                # A regulate() nudge may have left the follower at ±10%;
                # without an anchor the loop no longer corrects it.
                follower.player.set_speed(self.config.speed)
        if any(vid in self.engines for vid in affected):
            self._refresh_sync_ui()

    def _refresh_sync_ui(self) -> None:
        """Push the current sync state to the timeline and the chip."""
        offsets = self._current_offsets()
        self.timeline.set_anchors(self._current_anchors())
        self.timeline.set_offsets(offsets)
        self._set_sync_chip(offsets)

    def _align_current_frames(self) -> None:
        """One-shot sync: all current frames become the anchors."""
        if not self._require_ready():
            return
        self._stop_playback()
        offsets: dict[ViewId, float] = {}
        error: str | None = None
        for vid, eng in self.engines.items():
            if not eng.ready:
                continue
            try:
                offsets[vid] = eng.sync_here()
            except SyncError as exc:
                error = str(exc)
        if not offsets:
            logger.warning("align failed: %s", error)
            self.statusBar().showMessage(f"Cannot sync: {error or 'no pair ready'}")
            return
        rng = self._master_range()
        master = self.views["a"].player.time_pos
        if rng is not None and master is not None:
            self._seek_master_all(min(max(master, rng[0]), rng[1]))
        self._refresh_sync_ui()
        parts = [f"A{vid.upper()} {off:+.3f}s" for vid, off in offsets.items()]
        self.statusBar().showMessage("Synced: " + " · ".join(parts))

    def _set_sync_chip(self, offsets: dict[ViewId, float]) -> None:
        """Update the sync state chip (empty dict = not synced)."""
        if not offsets:
            self.sync_chip.setText("● not synced")
            self.sync_chip.setProperty("state", "off")
        else:
            if len(offsets) == 1:
                (off,) = offsets.values()
                self.sync_chip.setText(f"● {off:+.3f}s")
            else:
                inner = " · ".join(f"A{vid.upper()} {off:+.3f}" for vid, off in offsets.items())
                self.sync_chip.setText(f"● {inner}")
            self.sync_chip.setProperty("state", "on")
        # Force QSS re-evaluation after a dynamic property change.
        self.sync_chip.style().unpolish(self.sync_chip)
        self.sync_chip.style().polish(self.sync_chip)

    def _clear_sync(self) -> None:
        if not self._initialized:
            return
        self._stop_playback()
        for eng in self.engines.values():
            eng.clear_sync()
        # Undo any ±10% regulate nudge: without an anchor the loop stops
        # correcting, so a nudged follower would otherwise keep that speed.
        for vid in self.view_ids[1:]:
            self.views[vid].player.set_speed(self.config.speed)
        self._refresh_sync_ui()
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
        region = rec.physical_region(self.view_host)
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
            self._set_views_drag_enabled(False)
            if self._recorder is not None and self._recorder.output is not None:
                self.statusBar().showMessage(f"Recording → {self._recorder.output.name}")
        elif state == "stopping":
            self.btn_rec.setText("⏹ finishing…")
        else:  # finished / failed
            self.btn_rec.setText("⏺ Rec")
            self._set_rec_button_active(False)
            self._set_views_drag_enabled(True)

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

    # -- crosshair ------------------------------------------------------------------

    def _toggle_crosshair(self) -> None:
        """C key: flip the checkable button (toggled does the rest)."""
        self.btn_crosshair.setChecked(not self.btn_crosshair.isChecked())

    def _on_crosshair_toggled(self, checked: bool) -> None:
        """Show/hide the center crosshair on all views at once.

        One shared reference mark, not per-view: the point is comparing
        the views' motion against the same fixed position.
        """
        self.config.crosshair = checked
        self.config.save()
        if not self._initialized:
            return  # before initialize(); state applies on setChecked there
        for view in self.views.values():
            view.player.set_crosshair(checked)
        self.statusBar().showMessage("Crosshair on" if checked else "Crosshair off")

    # -- file dialog ------------------------------------------------------------------

    def _open_dialog(self, view: ViewId) -> None:
        if view not in self.views:
            return
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
        if not self._initialized:
            return
        # Recording elapsed time on the Rec button (once per second) runs
        # before the engine guard: recording works without loaded videos.
        # Only while truly RECORDING: in STOPPING the "finishing…" label
        # must not be overwritten by the timer.
        if self._recorder is not None and self._recorder.state is rec.RecorderState.RECORDING:
            secs = int(self._recorder.elapsed_seconds())
            if secs != self._rec_secs:
                self._rec_secs = secs
                self.btn_rec.setText(f"⏺ {secs // 60}:{secs % 60:02d}")
        a = self.views["a"].player
        master = a.time_pos
        if master is None:
            return  # view A not loaded yet
        # Follow each player's real state, not the global sync flag: a
        # solo-playing view must animate its own track.
        if a.is_playing:
            # playback-time is clock-interpolated: smoother than time_pos.
            pt = a.playback_time
            if pt is not None:
                master = pt
        shown = self._smooth_playhead(master, a.is_playing)
        # Each knob shows its own video's actual local time (so solo
        # playback moves only that track); the mapped position is a
        # fallback while a follower is mid-load and reports no time yet.
        heads: dict[ViewId, float] = {"a": shown}
        for vid in self.view_ids[1:]:
            p = self.views[vid].player
            t = p.playback_time
            if t is None:
                t = p.time_pos
            if t is None:
                eng = self.engines.get(vid)
                t = eng.view_time(vid, shown) if eng is not None else shown
            dur = p.duration
            if dur is not None:
                t = min(t, dur)
            heads[vid] = t
        self.timeline.set_playheads(heads)
        for vid in self.view_ids:
            self._update_view_time(self.views[vid])
            self.views[vid].set_play_text(self.views[vid].player.is_playing)
        self._status_live.setText(self._live_status_text(master))
        self.btn_play.setText("Pause" if self._playing else "Play")

    def _live_status_text(self, master: float) -> str:
        """Status-bar readout: exact per-pair detail until it stops fitting."""
        fps = self._reference_fps()
        offsets = self._current_offsets()
        drifts = {vid: eng.drift() for vid, eng in self.engines.items() if eng.ready}
        parts = [
            f"A{vid.upper()} {off:+.3f}s ({self._drift_ms(drifts.get(vid))})"
            for vid, off in offsets.items()
        ]
        if len(offsets) <= 1:
            self._status_live.setToolTip("")
            off = next(iter(offsets.values()), 0.0)
            # Prefer the drift of the synced pair itself; fall back to any
            # ready engine when nothing is synced (keeps the old readout).
            synced_vid = next(iter(offsets), None)
            drift = drifts.get(synced_vid) if synced_vid is not None else None
            if synced_vid is None:
                drift = next(iter(drifts.values()), None)
            return (
                f"A {master:8.3f}s | offset {off:+.3f}s "
                f"| drift {self._drift_ms(drift)} | {fps:g}fps"
            )
        if len(offsets) == 2:
            self._status_live.setToolTip("")
            return f"A {master:8.3f}s | {' | '.join(parts)} | {fps:g}fps"
        # 3 synced pairs: one line, per-pair detail in the tooltip. The
        # worst-case figure only considers synced pairs.
        self._status_live.setToolTip(" | ".join(parts))
        worst = max(
            (abs(d) for vid, d in drifts.items() if vid in offsets and d is not None),
            default=0.0,
        )
        return (
            f"A {master:8.3f}s | {len(offsets)} synced pairs "
            f"| max drift {worst * 1000:+.1f}ms | {fps:g}fps"
        )

    @staticmethod
    def _drift_ms(drift: float | None) -> str:
        return f"{drift * 1000:+.1f}ms" if drift is not None else "n/a"

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
        dur_a = self.views["a"].player.duration
        if dur_a is not None:
            est = min(max(est, 0.0), dur_a)
        return est

    @staticmethod
    def _update_view_time(view: VideoView) -> None:
        t = view.player.time_pos
        view.set_time_text(f"{t:.3f}s" if t is not None else "--")

    def _on_drift_tick(self) -> None:
        """Continuous sync regulation during free-run playback.

        Small drift is absorbed by nudging the follower's speed (up to
        +/-10%); a hard seek fires only beyond a few frames.
        Community-validated approach (Syncplay / mpv issue #13905).
        """
        if not self._initialized:
            return
        # Heartbeat before guards: proves the timer fires even when the
        # regulation path is skipped (unloaded / paused / not synced).
        self._drift_tick_count += 1
        if self._drift_tick_count % 100 == 0:  # every ~10s at 100ms tick
            logger.info(
                "drift tick alive: count=%d playing=%s views=%d ready=%d synced=%d",
                self._drift_tick_count,
                self._playing,
                len(self.view_ids),
                sum(1 for e in self.engines.values() if e.ready),
                sum(1 for e in self.engines.values() if e.state.is_complete),
            )
        if not self._playing:
            return
        for eng in self.engines.values():
            # An offset of 0 is not a sync: without an anchor, nudging a
            # follower's speed would drag an intentionally offset view
            # back onto A.
            if eng.ready and eng.state.is_complete:
                eng.regulate(self.config.speed)
