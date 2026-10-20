"""Main window: composition root for views, transport, timeline and sync.

Each pane keeps its own frame-step/play controls for the sync-point workflow,
while TransportController coordinates the ensemble. SessionController owns
view identity, reference promotion and follower engines; LoadCoordinator polls
asynchronous loads. Views are added (＋ / V) and removed (－), 2-4 total.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence, QShortcut, QShowEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..config import AppConfig
from ..core import recorder as rec
from ..core.player import PlayerError
from ..core.sync import ViewId
from . import layout as pane_layout
from .icons import make_icon
from .load_coordinator import LoadCoordinator
from .pane_drag import VIDEO_EXTS
from .recording_controller import RecordingController
from .session_controller import SessionController
from .status_presenter import (
    PlayheadSmoother,
    format_live_status,
    format_pair,
    format_sync_chip,
)
from .timeline import TimelineWidget
from .transport_controller import TransportController
from .video_view import VideoView
from .view_host import ViewHost
from .widgets import ChevronComboBox, set_style_property
from .win_chrome import apply_dark_title_bar

logger = logging.getLogger(__name__)

SPEED_STEPS = [0.1, 0.25, 0.5, 1.0, 2.0, 4.0]
UI_TICK_MS = 15  # ~66 Hz so the playhead tracks high-fps video smoothly
DRIFT_TICK_MS = 100

MIN_VIEWS = 2
MAX_VIEWS = pane_layout.MAX_VIEWS
ALL_VIEW_IDS: tuple[ViewId, ...] = ("a", "b", "c", "d")


class MainWindow(QMainWindow):
    """Top-level window assembling views, transport, timeline and sync logic."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self.setWindowTitle("video-compare-player")
        self.resize(config.window.width, config.window.height)

        self._session = SessionController([VideoView("A"), VideoView("B")])
        self._initialized = False
        # Row-list pane arrangement; ViewHost only moves widgets inside its
        # own grid (never reparents: the mpv surfaces are native windows).
        self._layout: pane_layout.Layout = pane_layout.default_layout(self.view_ids)
        # False while the arrangement is still the untouched default; a
        # user drag/preset flips it so add/remove preserves the layout.
        self._layout_customized = False
        self._drag_source: str | None = None
        self._track_hovered: ViewId | None = None
        self._drag_changed = False
        self._shortcuts: list[QShortcut] = []

        self.view_host = ViewHost()
        self.view_host.apply_layout(self._layout, self.views)
        self._rec_controller = RecordingController(self, self.view_host, self)

        self.timeline = TimelineWidget()
        self.timeline.setObjectName("timeline")
        self.timeline.set_tracks(self.view_ids)

        self.btn_first = self._icon_button("skip-start", "Go to start (Home)")
        self.btn_step_back = self._icon_button("step-back", "Step all views back one frame (←)")
        self.btn_step_fwd = self._icon_button("step-fwd", "Step all views forward one frame (→)")
        self.btn_last = self._icon_button("skip-end", "Go to end (End)")
        self.btn_play = QPushButton()
        self.btn_play.setObjectName("playButton")
        self.btn_play.setIcon(make_icon("play", "#ffffff"))
        self.btn_play.setIconSize(QSize(18, 18))
        self.btn_play.setToolTip("Play (Space)")
        self.btn_play.setAccessibleName("Play")
        self.btn_play.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for btn in (self.btn_first, self.btn_step_back, self.btn_step_fwd, self.btn_last):
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.speed_combo = ChevronComboBox()
        for s in SPEED_STEPS:
            self.speed_combo.addItem(f"{s:g}x", s)
        self.speed_combo.setCurrentIndex(
            SPEED_STEPS.index(config.speed) if config.speed in SPEED_STEPS else 3
        )
        self.speed_combo.setToolTip("Playback speed for all views (↑/↓)")

        self._open_actions: dict[ViewId, QAction] = {}
        self.btn_open = QToolButton()
        self.btn_open.setObjectName("toolButton")
        self.btn_open.setText("Open")
        self.btn_open.setIcon(make_icon("folder"))
        self.btn_open.setIconSize(QSize(16, 16))
        self.btn_open.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_open.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_open.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_open.setToolTip("Open a video for a view (Ctrl+O opens the active one)")
        open_menu = QMenu(self.btn_open)
        for vid in ALL_VIEW_IDS:
            action = open_menu.addAction(f"Open {vid.upper()}…")
            action.triggered.connect(lambda _=False, v=vid: self._open_dialog(v))
            self._open_actions[vid] = action
        open_menu.addSeparator()
        self._clear_all_action = open_menu.addAction("Clear all videos")
        self._clear_all_action.setIcon(make_icon("x", "#a1a1a6"))
        self._clear_all_action.triggered.connect(self._clear_all_videos)
        self.btn_open.setMenu(open_menu)

        self.btn_layout = QToolButton()
        self.btn_layout.setObjectName("toolButton")
        self.btn_layout.setText("Layout")
        self.btn_layout.setIcon(make_icon("layout"))
        self.btn_layout.setIconSize(QSize(16, 16))
        self.btn_layout.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_layout.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.btn_layout.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_layout.setToolTip("Arrange the panes (or drag a pane header)")
        layout_menu = QMenu(self.btn_layout)
        layout_menu.aboutToShow.connect(lambda: self._populate_layout_menu(layout_menu))
        self.btn_layout.setMenu(layout_menu)

        self.btn_add = self._icon_button("plus", "Add another comparison view (V)")
        self.btn_remove = self._icon_button("minus", "Remove the last view")
        self.btn_align = QPushButton("Align")
        self.btn_align.setObjectName("alignButton")
        self.btn_align.setIcon(make_icon("swap", "#a9a7ff"))
        self.btn_align.setIconSize(QSize(16, 16))
        self.btn_align.setToolTip(
            "Step each video to the same physical moment (e.g. stopwatch tick),\n"
            "then click this: all current frames become the sync anchors. (S)"
        )
        self.sync_chip = QLabel("● not synced")
        self.sync_chip.setObjectName("syncChip")
        self.sync_chip.setProperty("state", "off")
        self.btn_sync_clear = QPushButton("Unsync")
        self.btn_sync_clear.setObjectName("syncButton")
        self.btn_sync_clear.setIcon(make_icon("x", "#a1a1a6"))
        self.btn_sync_clear.setIconSize(QSize(14, 14))
        self.btn_sync_clear.setToolTip("Clear all sync points, keeping the videos (R)")
        self.btn_rec = QPushButton("Rec")
        self.btn_rec.setObjectName("recButton")
        self.btn_rec.setIcon(make_icon("record", "#ff453a"))
        self.btn_rec.setIconSize(QSize(14, 14))
        self.btn_rec.setToolTip(
            "Record the video views to an mp4 clip. (F9)\n"
            "Do not move or resize the window while recording."
        )
        self.btn_crosshair = QPushButton()
        self.btn_crosshair.setObjectName("crosshairButton")
        self.btn_crosshair.setIcon(make_icon("crosshair", "#ff9f0a"))
        self.btn_crosshair.setIconSize(QSize(16, 16))
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

        transport_bar = QFrame()
        transport_bar.setObjectName("transportBar")
        transport = QHBoxLayout(transport_bar)
        transport.setContentsMargins(12, 6, 12, 8)
        transport.setSpacing(6)
        left_group = QHBoxLayout()
        left_group.setSpacing(6)
        left_group.addWidget(self.btn_open)
        left_group.addWidget(self.btn_layout)
        left_group.addWidget(self._separator())
        left_group.addWidget(self.btn_add)
        left_group.addWidget(self.btn_remove)
        transport.addLayout(left_group)
        transport.addStretch(1)
        center_group = QHBoxLayout()
        center_group.setSpacing(2)
        center_group.addWidget(self.btn_first)
        center_group.addWidget(self.btn_step_back)
        center_group.addWidget(self.btn_play)
        center_group.addWidget(self.btn_step_fwd)
        center_group.addWidget(self.btn_last)
        transport.addLayout(center_group)
        transport.addStretch(1)
        right_group = QHBoxLayout()
        right_group.setSpacing(8)
        right_group.addWidget(self.speed_combo)
        right_group.addWidget(self._separator())
        right_group.addWidget(self.btn_align)
        right_group.addWidget(self.sync_chip)
        right_group.addWidget(self.btn_sync_clear)
        right_group.addWidget(self.btn_rec)
        right_group.addWidget(self.btn_crosshair)
        transport.addLayout(right_group)

        central = QWidget()
        central.setObjectName("centralContainer")
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(12, 12, 12, 12)
        vbox.setSpacing(10)
        vbox.addWidget(self.view_host, stretch=1)
        vbox.addWidget(self.timeline)
        vbox.addWidget(transport_bar)
        self.setCentralWidget(central)

        self._active_view = "a"
        self._loads = LoadCoordinator(self)
        self._load_failures: list[str] = []
        self._showing_load_failure = False
        self._transport = TransportController(self._session, config, self._notify)
        # Playhead smoothing state: interpolate between mpv frame updates.
        self._playhead = PlayheadSmoother()
        self._play_icon_state: bool | None = None
        self._closing = False
        self._drift_tick_count = 0
        # Live readout lives in a permanent widget: showMessage() stays free
        # for event messages ("Synced: ..."), which would otherwise be
        # clobbered by the 15 ms UI tick.
        self._status_live = QLabel("")
        self._status_live.setObjectName("statusLive")
        self.statusBar().addPermanentWidget(self._status_live)
        # Screen recorder session (created on first F9/Rec click).
        self._rec_secs = -1  # last elapsed second shown on the button

        self._ui_timer = QTimer(self)
        self._ui_timer.timeout.connect(self._on_ui_tick)
        self._drift_timer = QTimer(self)
        self._drift_timer.timeout.connect(self._on_drift_tick)
        self._loads.loaded.connect(self._on_video_loaded)
        self._loads.failed.connect(self._on_load_failed)
        self._loads.progress.connect(self._on_load_progress)

        self._refresh_view_buttons()
        self._set_active_view("a")

    @property
    def views(self) -> dict[ViewId, VideoView]:
        """Snapshot of the ordered view registry."""
        return self._session.views

    @property
    def view_ids(self) -> list[ViewId]:
        """View ids in track/display order."""
        return self._session.view_ids

    @property
    def _active_view(self) -> ViewId:
        active = self._session.active
        return active if active is not None else "a"

    @_active_view.setter
    def _active_view(self, view: ViewId) -> None:
        self._session.active = view

    # -- lifecycle --------------------------------------------------------------

    def initialize(self, videos: Sequence[Path]) -> None:
        """Create the sync engines and wire signals; call after the window is shown."""
        if self._initialized or self._closing:
            return  # single-shot entry; never double-wire shortcuts/signals
        try:
            self.views["a"].ensure_player()
            self.views["b"].ensure_player()
            self._session.rebuild()
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
        self.timeline.hovered_view.connect(self._on_track_hovered)
        self._rec_controller.message.connect(self.statusBar().showMessage)
        self._rec_controller.state_changed.connect(self._on_rec_state)
        self._rec_controller.saved.connect(self._on_rec_saved)
        self._rec_controller.error.connect(self._on_rec_error)

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

        # Apply persisted playback speed through the combo's sanitized
        # value: mpv defaults to 1.0x regardless of the combo box, and a
        # hand-edited config could hold a value outside SPEED_STEPS (combo
        # falls back to 1.0x) — routing through itemData keeps displayed,
        # applied and persisted speed identical by construction.
        speed = float(self.speed_combo.itemData(self.speed_combo.currentIndex()))
        self._transport.set_speed(speed)

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
        view.hover_changed.connect(self._on_view_hover_changed)
        view.context_menu_requested.connect(self._on_view_context_menu)
        view.clear_requested.connect(self._clear_view_video)
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

    def _icon_button(self, icon: str, tooltip: str) -> QPushButton:
        """Compact icon-only toolbar button (NoFocus, 16px glyph)."""
        btn = QPushButton()
        btn.setObjectName("iconButton")
        btn.setIcon(make_icon(icon))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tooltip)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return btn

    @staticmethod
    def _separator() -> QFrame:
        sep = QFrame()
        sep.setObjectName("toolbarSeparator")
        sep.setFixedSize(1, 18)
        return sep

    def _set_play_icon(self, playing: bool) -> None:
        if self._play_icon_state == playing:
            return
        self._play_icon_state = playing
        self.btn_play.setIcon(make_icon("pause" if playing else "play", "#ffffff"))
        self.btn_play.setToolTip("Pause (Space)" if playing else "Play (Space)")
        self.btn_play.setAccessibleName("Pause" if playing else "Play")

    def _populate_layout_menu(self, menu: QMenu) -> None:
        """(Re)build the layout menu; disabled while recording."""
        menu.clear()
        recording = self._recording()
        reset = menu.addAction("Reset layout")
        reset.triggered.connect(self._reset_layout)
        reset.setEnabled(not recording)
        presets = pane_layout.presets(self.view_ids)
        if not presets:
            return
        menu.addSeparator()
        for label, preset in presets:

            def apply_preset(_checked: bool = False, p: pane_layout.Layout = preset) -> None:
                self._set_layout(p)

            action = menu.addAction(label, apply_preset)
            action.setCheckable(True)
            action.setChecked(preset == self._layout)
            action.setEnabled(not recording)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        apply_dark_title_bar(self)

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
        self._transport.playing = False
        self._loads.clear()
        self._reset_hover()
        self._rec_controller.terminate()  # finalize the mp4 before we die
        self.config.window.width = self.width()
        self.config.window.height = self.height()
        self.config.save()
        for view in self.views.values():
            with contextlib.suppress(RuntimeError):
                view.player.terminate()  # player may never have been created
        super().closeEvent(event)

    # -- dynamic views --------------------------------------------------------

    def _notify(self, message: str) -> None:
        self.statusBar().showMessage(message)

    def _recording(self) -> bool:
        return self._rec_controller.recording

    def _guard_edit(self, action: str) -> bool:
        """Reject an edit while closing, uninitialized, or recording."""
        if self._closing:
            return False
        if not self._initialized:
            return False
        if self._recording():
            self.statusBar().showMessage(f"Stop recording before {action}")
            return False
        return True

    def _add_view(self, load: Path | None = None) -> None:
        """Append a new view (up to MAX_VIEWS); optionally load a video."""
        if not self._guard_edit("changing views"):
            return
        if len(self.view_ids) >= MAX_VIEWS:
            self.statusBar().showMessage(f"Maximum {MAX_VIEWS} views")
            return
        vid: ViewId = ALL_VIEW_IDS[len(self.view_ids)]
        if vid in self.views:
            logger.error("view id %s already registered; refusing to add", vid)
            return
        view = VideoView(vid.upper())
        # Parent the widget before creating its player: ensure_player() hands
        # the pane's native window to mpv, and reparenting afterwards could
        # recreate that handle. Every registration step is inside the try so
        # any failure rolls back to the exact pre-add state.
        prior_ids = list(self.view_ids)
        old_layout = [list(row) for row in self._layout]
        old_customized = self._layout_customized
        try:
            self._session.add_view(view)
            self._layout = pane_layout.add_view(
                self._layout, vid, prior_ids, customized=self._layout_customized
            )
            # Minimum sizes must be updated before the grid re-activates:
            # doing it afterwards leaves the top-level minimum stuck at the
            # previous, possibly larger, layout until the next resize event.
            self._apply_view_size_policy()
            self.view_host.apply_layout(self._layout, self.views)
            view.set_drag_enabled(not self._recording())
            view.ensure_player()
            self._session.add_follower(vid)
            self._wire_view(view, vid)
            self._connect_gestures(view)
            self._transport.apply_speed_to(view)
            if self.btn_crosshair.isChecked():
                view.player.set_crosshair(True)
        except Exception as exc:
            logger.warning("view %s failed to start: %s", vid, exc, exc_info=True)
            self._destroy_view(vid)
            self._layout = old_layout
            self._layout_customized = old_customized
            self._apply_view_size_policy()
            self.view_host.apply_layout(self._layout, self.views)
            self._refresh_view_buttons()
            QMessageBox.warning(self, "mpv error", str(exc))
            return
        self._refresh_view_buttons()
        self.timeline.set_tracks(self.view_ids)
        self._refresh_sync_ui()
        logger.info("view %s added (%d views)", vid, len(self.view_ids))
        if load is not None:
            self.open_video(vid, load)
        else:
            self.statusBar().showMessage(f"View {vid.upper()} added — drop or open a video")

    def _remove_last_view(self) -> None:
        """Tear down the most recently added view (A/B stay)."""
        if len(self.view_ids) <= MIN_VIEWS or not self._guard_edit("changing views"):
            return
        self._stop_playback()
        prior_ids = list(self.view_ids)
        vid = self.view_ids[-1]
        removed_master = vid == self._session.master
        new_master: ViewId | None = None
        if removed_master:
            new_master = self._reference_candidate(vid) or next(
                other for other in prior_ids if other != vid
            )
        self._layout = pane_layout.remove_view(
            self._layout, vid, prior_ids, customized=self._layout_customized
        )
        self._apply_view_size_policy()
        self._destroy_view(vid, replacement=new_master)
        if removed_master:
            self._transport.reset_speeds()
        self.view_host.apply_layout(self._layout, self.views)
        self._refresh_view_buttons()
        self._set_active_view(self._active_view)
        self.timeline.set_tracks(self.view_ids)
        self._reset_hover()
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
        # Keep tall layouts from forcing the window off-screen.
        min_h = 260 if rows == 1 else 150 if rows == 2 else 80
        for view in self.views.values():
            view.surface.setMinimumSize(min_w, min_h)

    def _destroy_view(self, vid: ViewId, *, replacement: ViewId | None = None) -> None:
        """Remove a view from state and life (player released)."""
        self._loads.cancel(vid)
        view = self._session.pop_view(vid, replacement=replacement)
        if view is not None:
            with contextlib.suppress(RuntimeError):
                view.player.terminate()
            self.view_host.remove_view(view)
            view.deleteLater()

    # -- pane arrangement ------------------------------------------------------

    def _reset_hover(self) -> None:
        """Clear pane/timeline hover state (drag, recording, removal)."""
        self.timeline.set_hovered_view(None)
        self._track_hovered = None
        for pane in self.views.values():
            set_style_property(pane, "trackHover", "")

    def _set_views_drag_enabled(self, enabled: bool) -> None:
        for view in self.views.values():
            view.set_drag_enabled(enabled)

    def _on_layout_drag_started(self, source: str) -> None:
        # The drag runs its own nested event loop: shortcuts would still
        # fire (Space, arrows, R, ...) while the pointer is captured.
        self._reset_hover()
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
        self._reset_hover()
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
            where = pane_layout.zone_preposition(zone)
            message = f"Release to insert {source.upper()} {where} {target.upper()}"
        self.statusBar().showMessage(message)

    def _on_view_hover_changed(self, view: str, hovered: bool) -> None:
        """Pane hover drives the matching timeline track highlight."""
        if self._drag_source is not None or self._closing or self._recording():
            return
        if view not in self.views:
            return
        if hovered:
            self.timeline.set_hovered_view(cast(ViewId, view))
        elif self.timeline.hovered_view_id == view:
            self.timeline.set_hovered_view(None)

    def _on_track_hovered(self, view: ViewId | None) -> None:
        """Timeline hover highlights the matching pane."""
        if self._drag_source is not None or self._closing or self._recording():
            return
        if view not in self.views:
            view = None
        if view == self._track_hovered:
            return
        previous = self._track_hovered
        self._track_hovered = view
        for vid in (previous, view):
            pane = self.views.get(vid) if vid is not None else None
            if pane is None:
                continue
            set_style_property(pane, "trackHover", "true" if vid == view else "")

    def _on_view_dropped_on(self, source: str, target: str, zone: str) -> None:
        if not self._guard_edit("rearranging views"):
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
            where = pane_layout.zone_preposition(zone)
            message = f"View {source.upper()} inserted {where} {target.upper()}"
        self.statusBar().showMessage(message)

    def _set_layout(self, new_layout: pane_layout.Layout, *, customized: bool = True) -> bool:
        """Apply a validated arrangement; False when nothing changed."""
        if not self._guard_edit("rearranging views"):
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
        self.view_host.apply_layout(self._layout, self.views)
        return True

    def _reset_layout(self) -> None:
        if not self._guard_edit("rearranging views"):
            return
        default = pane_layout.default_layout(self.view_ids)
        if self._set_layout(default, customized=False):
            self.statusBar().showMessage("Layout reset")
        else:
            self._layout_customized = False
            self.statusBar().showMessage("Layout already default")

    def _on_view_context_menu(self, _view_id: str, global_pos: QPoint) -> None:
        menu = QMenu(self)
        self._populate_layout_menu(menu)
        menu.exec(global_pos)
        menu.deleteLater()

    def _refresh_view_buttons(self) -> None:
        """Enable Open entries for existing views; gate ＋/－ by the limits."""
        can_edit = not self._recording()
        for vid, action in self._open_actions.items():
            action.setEnabled(vid in self.views and can_edit)
        self.btn_add.setEnabled(can_edit and len(self.view_ids) < MAX_VIEWS)
        self.btn_remove.setEnabled(can_edit and len(self.view_ids) > MIN_VIEWS)
        self._clear_all_action.setEnabled(can_edit)
        for view in self.views.values():
            view.btn_clear.setEnabled(can_edit)

    # -- loading / clearing -------------------------------------------------------

    def _reference_candidate(self, exclude: ViewId | None = None) -> ViewId | None:
        """Choose the next reference using the session's single policy."""
        return self._session.choose_reference(exclude)

    def _set_reference(self, vid: ViewId) -> bool:
        """Switch reference, rebuild engines, reset nudges, refresh UI."""
        if not self._session.set_master(vid):
            return False
        self._transport.reset_speeds()
        self._refresh_sync_ui()
        return True

    def _ensure_reference(self) -> None:
        """Promote a loaded view when the current reference has no video."""
        promoted = self._session.ensure_reference()
        if promoted is None:
            return
        self._transport.reset_speeds()
        self._refresh_sync_ui()
        logger.info("reference promoted to view %s", promoted)
        self.statusBar().showMessage(f"{promoted.upper()} is now the reference")

    def _clear_view_video(self, view: str) -> None:
        """Unload one pane's video and return it to the empty placeholder."""
        if not self._guard_edit("clearing videos"):
            return
        vid = cast(ViewId, view.lower())
        if vid not in self.views or not self._session.is_available(vid):
            return
        # Clearing the reference view promotes the first remaining loaded
        # view: the transport needs a master player and old anchors are void.
        new_master = self._reference_candidate(vid) if vid == self._session.master else None
        self._stop_playback()
        self._loads.cancel(vid)
        pane = self.views[vid]
        try:
            pane.player.unload()
        except Exception:  # mpv must not leave the pane visually loaded
            logger.warning("view %s unload failed", vid, exc_info=True)
        finally:
            pane.clear_loaded()
        if new_master is not None:
            self._set_reference(new_master)
        else:
            self._invalidate_sync(vid)
        self._push_timeline_durations()
        self._reset_hover()
        self._refresh_sync_ui()
        logger.info("view %s cleared", vid)
        if new_master is not None:
            self.statusBar().showMessage(
                f"View {vid.upper()} cleared — {new_master.upper()} is now the reference"
            )
        else:
            self.statusBar().showMessage(f"View {vid.upper()} cleared")

    def _clear_all_videos(self) -> None:
        """Unload every video, clear sync, and keep the pane layout."""
        if not self._guard_edit("clearing videos"):
            return
        if not any(self._session.is_available(vid) for vid in self.view_ids):
            self.statusBar().showMessage("No videos to clear")
            return
        self._stop_playback()
        self._loads.clear()
        for vid in self.view_ids:
            pane = self.views[vid]
            try:
                if pane.surface.has_player:
                    pane.player.unload()
            except Exception:
                logger.warning("view %s unload failed", vid, exc_info=True)
            finally:
                pane.clear_loaded()
        # Do not rely on rebuild()'s side effect: set_master is idempotent.
        self._session.clear_sync()
        self._session.set_master("a")
        self._transport.reset_speeds()
        self._push_timeline_durations()
        self._reset_hover()
        self._refresh_sync_ui()
        logger.info("all videos cleared")
        self.statusBar().showMessage("All videos cleared")

    def _push_timeline_durations(self) -> None:
        """Send each view's duration to the timeline when it changes."""
        durs: dict[ViewId, float | None] = {}
        for vid in self.view_ids:
            pane = self.views[vid]
            # A cleared/failed pane stays dead even if mpv still reports a
            # stale duration from the previous file.
            if pane.player.is_loading or pane.player.is_ready:
                durs[vid] = pane.player.duration
            else:
                durs[vid] = None
        self.timeline.set_durations(durs)

    def open_video(self, view: ViewId, path: Path) -> None:
        """Load a video into one of the views."""
        if view not in self.views or not self._guard_edit("opening videos"):
            return
        self._stop_playback()
        try:
            self.views[view].player.load(path)
        except PlayerError as exc:
            QMessageBox.warning(self, "open failed", str(exc))
            return
        # Only a successfully accepted load invalidates the old file's sync:
        # a failed dialog/file pick leaves the current session untouched.
        # (A reload of A breaks every pair, a follower only its own.)
        self._invalidate_sync(view)
        self._loads.start(view, path, self.views[view].player)
        self.config.last_dir = str(path.parent)
        logger.info("view %s <- %s", view, path)

    def _on_load_progress(self) -> None:
        """Keep durations/reference current while a load is still demuxing."""
        if not self._initialized or self._closing:
            return
        self._push_timeline_durations()
        self._ensure_reference()

    def _on_video_loaded(self, view_id: str, path: object, _meta: object) -> None:
        """A load finished: update the pane and refresh dependent state."""
        if self._closing:
            return
        vid = cast(ViewId, view_id)
        view = self.views.get(vid)
        if view is None:
            return
        name = Path(str(path)).name
        view.set_loaded_name(name)
        self._notify(f"{vid.upper()}: {name} loaded")
        self._push_timeline_durations()
        self._ensure_reference()
        self._refresh_view_buttons()

    def _on_load_failed(self, view_id: str, path: object, message: str) -> None:
        """A load failed: make the pane consistent, then report asynchronously."""
        if self._closing:
            return
        vid = cast(ViewId, view_id)
        view = self.views.get(vid)
        if view is not None:
            view.clear_loaded()
        self._push_timeline_durations()
        self._ensure_reference()
        self._refresh_view_buttons()
        logger.warning("view %s failed to load %s", vid, path)
        self._queue_load_failure(message)

    def _queue_load_failure(self, message: str) -> None:
        self._load_failures.append(message)
        if not self._showing_load_failure:
            self._showing_load_failure = True
            QTimer.singleShot(0, self._show_next_load_failure)

    def _show_next_load_failure(self) -> None:
        if self._closing or not self._load_failures:
            self._showing_load_failure = False
            return
        message = self._load_failures.pop(0)
        QMessageBox.warning(self, "open failed", message)
        if self._load_failures:
            QTimer.singleShot(0, self._show_next_load_failure)
        else:
            self._showing_load_failure = False

    # -- transport ------------------------------------------------------------------

    def _seek_master_all(self, master: float, *, exact: bool = True) -> None:
        """Seek the master once and every ready follower to one position."""
        self._transport.seek_master_all(master, exact=exact)

    def _toggle_play(self) -> None:
        self._transport.toggle()

    def _stop_playback(self) -> None:
        self._transport.stop()

    def _resync_if_needed(self) -> None:
        if not self._closing:
            self._transport.resync_if_needed()

    def _step(self, frames: int) -> None:
        self._transport.step(frames)

    def _step_view(self, view: ViewId, frames: int) -> None:
        """Step one view independently (used to find the sync frame)."""
        self._transport.step_view(view, frames)

    def _toggle_play_view(self, view: ViewId) -> None:
        """Play/pause one view alone, for hunting the sync frame."""
        self._transport.toggle_view(view)

    def _goto_first(self) -> None:
        self._transport.goto_first()

    def _goto_last(self) -> None:
        self._transport.goto_last()

    def _on_scrub_preview(self, t: float) -> None:
        """Coarse seek while dragging; stops playback so they don't fight."""
        self._transport.scrub_preview(t)

    def _on_seek_committed(self, t: float) -> None:
        """Exact seek on mouse release."""
        self._transport.seek_committed(t)

    def _on_speed_changed(self, index: int) -> None:
        speed = float(self.speed_combo.itemData(index))
        self._transport.set_speed(speed)

    def _cycle_speed(self, direction: int) -> None:
        idx = min(len(SPEED_STEPS) - 1, max(0, self.speed_combo.currentIndex() + direction))
        self.speed_combo.setCurrentIndex(idx)

    # -- sync ------------------------------------------------------------------

    def _set_active_view(self, view: str) -> None:
        vid = cast(ViewId, view.lower())
        if vid not in self.views:
            return
        self._active_view = vid
        for pane_id, pane in self.views.items():
            set_style_property(pane, "activePane", "true" if pane_id == vid else "")

    def _invalidate_sync(self, view: ViewId) -> None:
        """Drop sync anchors that involve a (re)loaded view."""
        affected = self._session.affected_by(view)
        cleared = self._session.invalidate(view)
        for vid in affected:
            self._transport.reset_speed(vid)
        if cleared:
            self._refresh_sync_ui()

    def _refresh_sync_ui(self) -> None:
        """Push the current sync state to the timeline and the chip."""
        offsets = self._session.current_offsets()
        self.timeline.set_master(self._session.master)
        self.timeline.set_anchors(self._session.current_anchors())
        self.timeline.set_offsets(offsets)
        self._set_sync_chip(offsets)

    def _align_current_frames(self) -> None:
        """One-shot sync: all current frames become the anchors."""
        if not self._transport.any_engine_ready():
            self.statusBar().showMessage("Load at least two videos first")
            return
        self._stop_playback()
        offsets, error = self._session.align_current()
        if not offsets:
            logger.warning("align failed: %s", error)
            self.statusBar().showMessage(f"Cannot sync: {error or 'no pair ready'}")
            return
        rng = self._transport.master_range()
        master = self.views[self._session.master].player.time_pos
        if rng is not None and master is not None:
            self._seek_master_all(min(max(master, rng[0]), rng[1]))
        self._refresh_sync_ui()
        parts = [format_pair(self._session.master, vid, off) for vid, off in offsets.items()]
        message = "Synced: " + " · ".join(parts)
        if error:
            message += f" · skipped: {error}"
        self.statusBar().showMessage(message)

    def _set_sync_chip(self, offsets: dict[ViewId, float]) -> None:
        """Update the sync state chip (empty dict = not synced)."""
        text, state = format_sync_chip(offsets, self._session.master)
        self.sync_chip.setText(text)
        set_style_property(self.sync_chip, "state", state)

    def _clear_sync(self) -> None:
        if not self._initialized:
            return
        self._stop_playback()
        self._session.clear_sync()
        # Undo any ±10% regulate nudge: without an anchor the loop stops
        # correcting, so a nudged follower would otherwise keep that speed.
        self._transport.reset_speeds()
        self._refresh_sync_ui()
        self.statusBar().showMessage("Sync cleared")

    # -- recording ------------------------------------------------------------------

    def _toggle_record(self) -> None:
        """F9 / Rec button: start recording, or stop when one is running."""
        self._rec_controller.toggle()

    def _on_rec_state(self, state: str) -> None:
        if self._closing:
            return
        if state == "recording":
            self._rec_secs = -1
            self.btn_rec.setText("⏺ 0:00")
            self._set_rec_button_active(True)
            self._set_views_drag_enabled(False)
            self._reset_hover()
            self._refresh_view_buttons()
            output_name = self._rec_controller.output_name
            if output_name is not None:
                self.statusBar().showMessage(f"Recording → {output_name}")
        elif state == "stopping":
            self.btn_rec.setText("⏹ finishing…")
        else:  # finished / failed
            self.btn_rec.setText("⏺ Rec")
            self._set_rec_button_active(False)
            self._set_views_drag_enabled(True)
            self._reset_hover()
            self._rec_secs = -1
            self._refresh_view_buttons()

    def _set_rec_button_active(self, active: bool) -> None:
        set_style_property(self.btn_rec, "rec", "on" if active else "off")

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
        if not self._initialized or self._closing:
            return
        # Recording elapsed time on the Rec button (once per second) runs
        # before the engine guard: recording works without loaded videos.
        # Only while truly RECORDING: in STOPPING the "finishing…" label
        # must not be overwritten by the timer.
        if self._rec_controller.state is rec.RecorderState.RECORDING:
            secs = int(self._rec_controller.elapsed_seconds())
            if secs != self._rec_secs:
                self._rec_secs = secs
                self.btn_rec.setText(f"⏺ {secs // 60}:{secs % 60:02d}")
        if self._session.master not in self.views:
            self._ensure_reference()
            if self._session.master not in self.views:
                return
        self._transport.reconcile()
        master_player = self.views[self._session.master].player
        master = master_player.time_pos
        shown: float | None = None
        if master is not None:
            # Follow each player's real state, not the global sync flag: a
            # solo-playing view must animate its own track.
            if master_player.is_playing:
                # playback-time is clock-interpolated: smoother than time_pos.
                pt = master_player.playback_time
                if pt is not None:
                    master = pt
            shown = self._playhead.update(
                master,
                master_player.is_playing,
                self.config.speed,
                master_player.duration,
            )
        # Each knob shows its own video's actual local time (so solo
        # playback moves only that track); the mapped position is a
        # fallback while a follower is mid-load and reports no time yet.
        # Follower readouts keep updating even when the reference is empty.
        heads: dict[ViewId, float] = {} if shown is None else {self._session.master: shown}
        for vid in self.view_ids:
            if vid == self._session.master:
                continue
            p = self.views[vid].player
            t = p.playback_time
            if t is None:
                t = p.time_pos
            if t is None and shown is not None:
                t = self._session.view_time(vid, shown, self._session.master)
            if t is None:
                continue
            dur = p.duration
            if dur is not None:
                t = min(t, dur)
            heads[vid] = t
        if heads:
            self.timeline.set_playheads(heads)
        for vid in self.view_ids:
            self._update_view_time(self.views[vid])
            self.views[vid].set_play_text(self.views[vid].player.is_playing)
        if shown is not None:
            assert master is not None
            readout = format_live_status(
                master,
                self._session.current_offsets(),
                self._session.drifts(),
                self._transport.reference_fps(),
                self._session.master,
            )
            self._status_live.setText(readout.text)
            self._status_live.setToolTip(readout.tooltip)
        else:
            self._status_live.setText(f"{self._session.master.upper()} not loaded")
        self._set_play_icon(self._transport.playing)

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
        if not self._initialized or self._closing:
            return
        self._drift_tick_count += 1
        if self._drift_tick_count % 100 == 0:  # every ~10s at 100ms tick
            logger.debug(
                "drift tick: playing=%s views=%d ready=%d synced=%d",
                self._transport.playing,
                len(self.view_ids),
                sum(1 for e in self._session.engines.values() if e.ready),
                sum(1 for e in self._session.engines.values() if e.state.is_complete),
            )
        self._transport.regulate()
