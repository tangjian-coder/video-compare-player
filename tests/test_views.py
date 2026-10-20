"""Offscreen GUI tests for dynamic view add/remove bookkeeping.

Runs the real MainWindow/VideoView widgets on Qt's offscreen platform with
stub players, exercising the ＋/－ bookkeeping that the live smoke test
cannot reach without mouse automation.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before QApplication

from typing import Any

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent, QPointingDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from vcplayer.config import AppConfig
from vcplayer.core.player import PlayerError
from vcplayer.ui import layout as pane_layout
from vcplayer.ui.main_window import MainWindow
from vcplayer.ui.timeline import TRACK_H, TRACK_PITCH, TRACK_TOP_Y, TimelineWidget
from vcplayer.ui.video_view import VideoView


class FakePlayer:
    """Just enough PlayerController surface for the window code paths."""

    def __init__(self) -> None:
        self.is_playing = False
        self.meta: Any = None
        self.duration: float | None = None
        self.time_pos: float | None = 0.0
        self.fps: float | None = 60.0
        self.rotation = 0
        self.playback_time: float | None = None
        self.seeks: list[float] = []
        self.fast_seeks: list[float] = []
        self.speeds: list[float] = []
        self.plays = 0
        self.unloads = 0
        self.load_error: str | None = None
        self._loading = False
        self._failed = False

    @property
    def is_ready(self) -> bool:
        return self.meta is not None

    @property
    def is_loading(self) -> bool:
        return self._loading

    @property
    def is_failed(self) -> bool:
        return self._failed

    def load(self, path: Any) -> None:
        self._loading = True
        self._failed = False
        self.load_error = None
        self.meta = None
        self.duration = None

    def mark_load_failed(self, message: str) -> None:
        self._loading = False
        self._failed = True
        self.load_error = message
        self.meta = None

    def play(self) -> None:
        self.plays += 1
        self.is_playing = True

    def pause(self) -> None:
        self.is_playing = False

    def terminate(self) -> None: ...

    def unload(self) -> None:
        self.unloads += 1
        self.duration = None
        self.meta = None
        self.time_pos = None
        self.is_playing = False
        self._loading = False
        self._failed = False

    def set_speed(self, speed: float) -> None:
        self.speeds.append(speed)

    def set_crosshair(self, enabled: bool) -> None: ...

    def rotate_cw(self) -> None:
        self.rotation = (self.rotation + 90) % 360

    def seek_exact(self, t: float) -> None:
        self.seeks.append(t)
        self.time_pos = t

    def seek_fast(self, t: float) -> None:
        self.fast_seeks.append(t)
        self.time_pos = t


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_window(qapp: QApplication, n: int = 2) -> MainWindow:
    """A MainWindow with `n` stub-player views and initialized flag set."""
    w = MainWindow(AppConfig())
    for vid in ("a", "b"):
        w.views[vid].surface._player = FakePlayer()  # type: ignore[assignment]
    for i in range(2, n):
        vid = ("a", "b", "c", "d")[i]
        view = VideoView(vid.upper())
        view.surface._player = FakePlayer()  # type: ignore[assignment]
        prior = list(w.view_ids)
        w._session.add_view(view)
        w._layout = pane_layout.add_view(w._layout, vid, prior)
        w.view_host.apply_layout(w._layout, w.views)
    w._initialized = True
    w._refresh_view_buttons()
    w.timeline.set_tracks(w.view_ids)
    return w


def test_remove_last_view_full_bookkeeping(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    assert w.view_ids == ["a", "b", "c"]
    w._active_view = "c"
    w._remove_last_view()
    assert w.view_ids == ["a", "b"]
    assert "c" not in w.views
    assert "c" not in w._session.engines
    assert "c" not in w._loads.pending_ids
    assert w.view_host.count == 2
    assert w.timeline._views == ["a", "b"]
    assert w._active_view == "a"  # fell back from the removed view


def test_remove_refuses_below_min(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._remove_last_view()
    assert w.view_ids == ["a", "b"]
    assert w.view_host.count == 2


def test_add_view_refuses_before_initialize(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._initialized = False
    w._add_view()
    assert w.view_ids == ["a", "b"]


def test_view_button_gating(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    assert w.btn_add.isEnabled() and not w.btn_remove.isEnabled()
    w2 = make_window(qapp, n=4)
    assert not w2.btn_add.isEnabled() and w2.btn_remove.isEnabled()
    for _vid, action in w2._open_actions.items():
        assert action.isEnabled()
    w3 = make_window(qapp, n=3)
    assert w3._open_actions["a"].isEnabled()
    assert not w3._open_actions["d"].isEnabled()


def test_four_views_default_to_2x2(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    assert w._layout == [["a", "b"], ["c", "d"]]


def test_drop_on_left_edge_inserts_in_target_row(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    w._on_view_dropped_on("d", "a", pane_layout.ZONE_LEFT)
    assert w._layout == [["d", "a", "b"], ["c"]]
    assert w.view_host.count == 4


def test_drop_on_center_swaps(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    w._on_view_dropped_on("a", "d", pane_layout.ZONE_CENTER)
    assert w._layout == [["d", "b"], ["c", "a"]]


def test_drop_is_ignored_while_recording(qapp: QApplication) -> None:
    class FakeRecorder:
        recording = True

    w = make_window(qapp, n=4)
    w._rec_controller._recorder = FakeRecorder()  # type: ignore[assignment]
    w._on_view_dropped_on("d", "a", pane_layout.ZONE_LEFT)
    assert w._layout == [["a", "b"], ["c", "d"]]


def _players(w: MainWindow) -> dict[str, FakePlayer]:
    return {vid: view.surface._player for vid, view in w.views.items()}  # type: ignore[misc]


def test_seek_master_all_clamps_to_common_range(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["b"].duration, p["c"].duration = 100.0, 50.0, 80.0
    p["a"].meta = p["b"].meta = p["c"].meta = object()
    w._session.add_follower("b")
    w._session.add_follower("c")
    w._seek_master_all(70.0)  # beyond b's 50s: the common range is (0, 50)
    # Every view lands on the same clamped master; A is seeked exactly once.
    assert p["a"].seeks == [50.0]
    assert p["b"].seeks == [50.0]
    assert p["c"].seeks == [50.0]


def test_seek_master_all_without_common_range_does_not_seek(
    qapp: QApplication,
) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["b"].duration, p["c"].duration = 100.0, 5.0, 2.0
    p["a"].meta = p["b"].meta = p["c"].meta = object()
    w._session.add_follower("b")
    w._session.add_follower("c")
    # Disjoint ranges: offset -6 puts A-C at (6, 8), A-B at (0, 5).
    w._session.engines["c"].state.point_a, w._session.engines["c"].state.point_b = 6.0, 0.0
    w._seek_master_all(1.0)
    assert p["a"].seeks == []
    assert p["b"].seeks == []
    assert p["c"].seeks == []


def test_remove_synced_view_refreshes_chip(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._session.add_follower("b")
    w._session.add_follower("c")
    for vid in ("b", "c"):
        w._session.engines[vid].state.point_a = 1.0
        w._session.engines[vid].state.point_b = 2.0
    w._refresh_sync_ui()
    assert "AC" in w.sync_chip.text()
    w._remove_last_view()
    assert "AC" not in w.sync_chip.text()
    assert w.sync_chip.text() == "● +1.000s"


def test_toggle_play_skips_views_still_loading(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["b"].duration = 10.0, 10.0
    p["a"].meta = object()  # loaded
    p["b"].meta = object()  # loaded
    p["c"].meta = None  # still demuxing
    w._session.add_follower("b")
    w._toggle_play()
    assert p["a"].plays == 1
    assert p["b"].plays == 1
    assert p["c"].plays == 0  # must not start from 0 once the file opens


def test_toggle_play_while_loading_does_not_enter_ensemble(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].duration = p["b"].duration = 10.0
    p["a"].meta = p["b"].meta = None  # still demuxing
    w._session.add_follower("b")
    w._toggle_play()
    assert not w._transport.playing
    assert p["a"].plays == 0
    assert p["b"].plays == 0


def test_toggle_play_refuses_when_reference_is_loading(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].duration = p["b"].duration = 10.0
    p["a"].meta = None  # reference still demuxing
    p["b"].meta = object()
    w._session.add_follower("b")
    w._toggle_play()
    assert not w._transport.playing
    assert p["b"].plays == 0
    assert "reference" in w.statusBar().currentMessage()


def test_reconcile_stops_ensemble_at_reference_eof(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].duration = p["b"].duration = 10.0
    p["a"].meta = p["b"].meta = object()
    p["a"].time_pos = p["b"].time_pos = 10.0
    p["a"].is_playing = p["b"].is_playing = True
    w._session.add_follower("b")
    w._transport.playing = True
    w._on_ui_tick()
    assert not w._transport.playing
    assert not p["a"].is_playing and not p["b"].is_playing


def test_global_play_replaces_solo_playback(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].duration = p["b"].duration = 10.0
    p["a"].meta = p["b"].meta = object()
    p["b"].is_playing = True
    w._session.add_follower("b")
    assert not w._transport.playing
    w._on_ui_tick()
    assert not w._transport.playing
    w._toggle_play()
    assert w._transport.playing
    assert p["a"].plays == 1
    assert p["b"].plays == 1


def test_failed_reload_promotes_another_reference(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
        player.meta = object()
    for view in w.views.values():
        view.set_loaded_name("video.mp4")
    monkeypatch.setattr("vcplayer.ui.main_window.QMessageBox.warning", lambda *a, **k: None)
    p["a"].mark_load_failed("bad file")
    w._on_load_failed("a", Path("bad.mp4"), "bad file")
    qapp.processEvents()  # drain the queued failure toast while patched
    assert w._session.master == "b"
    assert w.views["a"]._caption.text() == "no video"
    assert not w._session.engines["a"].ready


def test_failed_follower_cannot_stay_engine_ready(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
        player.meta = object()
    for vid in ("b", "c"):
        w._session.add_follower(vid)
    monkeypatch.setattr("vcplayer.ui.main_window.QMessageBox.warning", lambda *a, **k: None)
    p["b"].mark_load_failed("bad file")
    w._on_load_failed("b", Path("bad.mp4"), "bad file")
    qapp.processEvents()
    assert w._session.engines["b"].ready is False
    assert w._session.engines["c"].ready is True


def test_min_size_policy_follows_layout(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)  # default 2x2: two panes wide, two rows
    w._apply_view_size_policy()
    for view in w.views.values():
        assert view.surface.minimumWidth() == 400
        assert view.surface.minimumHeight() == 150
    w2 = make_window(qapp, n=2)
    w2._apply_view_size_policy()
    for view in w2.views.values():
        assert view.surface.minimumWidth() == 400
        assert view.surface.minimumHeight() == 260
    w2._set_layout([["a", "b"], ["c", "d"]])  # rejected: wrong view set
    assert w2._layout == [["a", "b"]]


def test_drag_chip_is_compact_and_titled(qapp: QApplication) -> None:
    view = VideoView("A")
    pixmap = view._header._drag_pixmap()
    assert not pixmap.isNull()
    width = pixmap.width() / pixmap.devicePixelRatio()
    height = pixmap.height() / pixmap.devicePixelRatio()
    assert 140 <= width <= 220
    assert 30 <= height <= 60
    view.set_loaded_name("2026-01-01_take_0001_with_a_long_name.mp4")
    assert view._header._title.endswith(".mp4")
    assert view._caption.toolTip().endswith(".mp4")


def test_drag_source_border_property(qapp: QApplication) -> None:
    view = VideoView("A")
    view.set_drag_source(True)
    assert view.property("dragSource") == "true"
    view.set_drag_source(False)
    assert view.property("dragSource") == ""


def test_host_indicator_only_for_edge_zones(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    pane = w.views["a"]
    w.view_host.set_drop_indicator(pane, pane_layout.ZONE_LEFT)
    assert w.view_host._indicator == (pane, pane_layout.ZONE_LEFT)
    w.view_host.set_drop_indicator(pane, pane_layout.ZONE_CENTER)
    assert w.view_host._indicator is None


def test_layout_change_preserves_view_identity(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    ids = list(w.view_ids)
    assert w._set_layout([["d", "c"], ["b", "a"]])
    assert w.view_ids == ids
    assert w.timeline._views == ids
    assert w.view_host.views["a"] is w.views["a"]


def test_recording_blocks_presets_and_reset(qapp: QApplication) -> None:
    class FakeRecorder:
        recording = True

    w = make_window(qapp, n=4)
    w._rec_controller._recorder = FakeRecorder()  # type: ignore[assignment]
    assert not w._set_layout([["a", "b", "c", "d"]])
    assert w._layout == [["a", "b"], ["c", "d"]]
    w._reset_layout()
    assert w._layout == [["a", "b"], ["c", "d"]]


def test_drag_finish_clears_shortcuts_and_drop_state(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    pane = w.views["a"]
    pane.set_drag_source(True)
    pane.set_drop_zone(pane_layout.ZONE_LEFT)
    w.view_host._drop_pane = pane
    w._on_layout_drag_finished()
    assert pane.property("dragSource") == ""
    assert pane.property("dropZone") == ""
    assert w.view_host._indicator is None
    assert w.view_host._drop_pane is None


def test_real_add_remove_follows_default_layouts(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=2)

    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    w._add_view()
    assert w._layout == [["a", "b", "c"]]
    w._add_view()
    assert w._layout == [["a", "b"], ["c", "d"]]
    assert w.view_host.count == 4
    w._remove_last_view()
    assert w._layout == [["a", "b", "c"]]
    w._remove_last_view()
    assert w._layout == [["a", "b"]]
    assert w.view_host.count == 2


def test_real_add_remove_keeps_custom_arrangement(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=3)
    assert w._set_layout([["a", "b"], ["c"]])  # Top 2 + 1 preset

    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    w._add_view()
    assert w._layout == [["a", "b"], ["c", "d"]]
    w._remove_last_view()
    assert w._layout == [["a", "b"], ["c"]]


def test_rotation_angle_uses_reserved_label(qapp: QApplication) -> None:
    view = VideoView("A")
    view.surface._player = FakePlayer()  # type: ignore[assignment]
    view._set_rotate_text()
    assert view._rotation_label.text() == ""
    assert view.btn_rotate.text() == ""
    view._on_rotate()
    assert view._rotation_label.text() == "90°"
    assert view.btn_rotate.text() == ""
    view._on_rotate()
    assert view._rotation_label.text() == "180°"
    assert view._rotation_label.width() == 30


def test_pane_hover_signal_is_deduplicated(qapp: QApplication) -> None:
    view = VideoView("A")
    seen: list[tuple[str, bool]] = []
    view.hover_changed.connect(lambda vid, hovered: seen.append((vid, hovered)))
    view._set_hovered(True)
    view._set_hovered(True)
    view._set_hovered(False)
    assert seen == [("a", True), ("a", False)]


def test_track_hover_sets_pane_property(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._on_track_hovered("b")
    assert w.views["b"].property("trackHover") == "true"
    assert w.views["a"].property("trackHover") == ""
    w._on_track_hovered(None)
    assert all(view.property("trackHover") == "" for view in w.views.values())


def test_view_hover_drives_timeline(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w.timeline.set_durations({"a": 10.0, "b": 10.0, "c": 10.0})
    w._on_view_hover_changed("b", True)
    assert w.timeline.hovered_view_id == "b"
    w._on_view_hover_changed("b", False)
    assert w.timeline.hovered_view_id is None
    w._drag_source = "a"
    w._on_view_hover_changed("b", True)
    assert w.timeline.hovered_view_id is None


def test_add_view_rolls_back_on_unexpected_error(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=2)
    before_views = dict(w.views)
    before_layout = [list(row) for row in w._layout]
    before_ids = list(w.view_ids)
    monkeypatch.setattr(
        VideoView, "ensure_player", lambda self: (_ for _ in ()).throw(ValueError("boom"))
    )
    monkeypatch.setattr("vcplayer.ui.main_window.QMessageBox.warning", lambda *a, **k: None)
    w._add_view()
    assert w.view_ids == before_ids
    assert w.views.keys() == before_views.keys()
    assert w._layout == before_layout
    assert w.view_host.count == 2
    assert w._session.engines == {}


def test_failed_open_preserves_sync(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].duration, p["b"].duration = 10.0, 10.0
    w._session.add_follower("b")
    w._session.engines["b"].state.point_a = 1.0
    w._session.engines["b"].state.point_b = 2.0
    monkeypatch.setattr(
        p["b"],
        "load",
        lambda path: (_ for _ in ()).throw(PlayerError("missing")),
        raising=False,
    )
    monkeypatch.setattr("vcplayer.ui.main_window.QMessageBox.warning", lambda *a, **k: None)
    w.open_video("b", Path("missing.mp4"))
    assert w._session.engines["b"].state.is_complete
    assert "b" not in w._loads.pending_ids


def test_recording_blocks_open_hover_and_open_actions(qapp: QApplication) -> None:
    from pathlib import Path

    class FakeRecorder:
        recording = True

    w = make_window(qapp, n=3)
    w._rec_controller._recorder = FakeRecorder()  # type: ignore[assignment]
    w._refresh_view_buttons()
    assert not any(action.isEnabled() for action in w._open_actions.values())
    w._on_view_hover_changed("a", True)
    assert w.timeline.hovered_view_id is None
    w._on_track_hovered("a")
    assert w.views["a"].property("trackHover") != "true"
    w.open_video("a", Path("x.mp4"))
    assert not w._loads.pending_ids


def test_timeline_tracks_and_hover_reset_on_remove(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w.timeline.set_durations({"c": 10.0})
    w.timeline.set_hovered_view("c")
    w._on_track_hovered("c")
    w._remove_last_view()
    assert w.timeline.hovered_view_id is None
    assert w._track_hovered is None
    assert all(view.property("trackHover") != "true" for view in w.views.values())


def test_timeline_mouse_tracking_and_key_filtering(qapp: QApplication) -> None:
    t = TimelineWidget()
    assert t.hasMouseTracking()
    t.set_tracks(["a", "b"])
    t.set_durations({"a": 10.0, "x": 99.0})
    t.set_anchors({"x": 1.0})
    t.set_offsets({"x": 1.0})
    t.set_playheads({"x": 1.0})
    assert "x" not in t._dur and "x" not in t._anchor
    assert "x" not in t._offsets and "x" not in t._t
    assert t._span == 10.0
    t.set_hovered_view("b")
    t.set_tracks(["a"])  # drops b and its highlight
    assert t.hovered_view_id is None


def test_timeline_move_event_drives_hover(qapp: QApplication) -> None:
    t = TimelineWidget()
    t.set_tracks(["a", "b"])
    t.set_durations({"a": 10.0, "b": 10.0})
    seen: list[str | None] = []
    t.hovered_view.connect(seen.append)
    y = 20.0  # inside the first track row
    event = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(100.0, y),
        QPointF(100.0, y),
        QPointF(100.0, y),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        QPointingDevice.primaryPointingDevice(),
    )
    t.mouseMoveEvent(event)
    assert seen and seen[-1] in ("a", "b")
    t.leaveEvent(QEvent(QEvent.Type.Leave))
    assert seen[-1] is None


def test_unloaded_timeline_track_is_inert(qapp: QApplication) -> None:
    t = TimelineWidget()
    t.set_tracks(["a", "b", "c"])
    t.set_durations({"a": 10.0, "b": 10.0})
    seen: list[float] = []
    t.scrub_preview.connect(seen.append)
    t.seek_committed.connect(seen.append)
    y = TRACK_TOP_Y + 2 * TRACK_PITCH + TRACK_H / 2
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(100.0, y),
        QPointF(100.0, y),
        QPointF(100.0, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPointingDevice.primaryPointingDevice(),
    )
    t.mousePressEvent(event)
    assert t._drag_view is None
    assert seen == []


def test_empty_pane_hover_does_not_light_track_or_pane(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w.timeline.hovered_view.connect(w._on_track_hovered)
    w.timeline.set_durations({"a": 10.0, "b": 10.0})
    w.timeline._set_hover("b")
    assert w.views["b"].property("trackHover") == "true"
    w._on_view_hover_changed("c", True)
    assert w.timeline.hovered_view_id is None
    assert w.views["b"].property("trackHover") == ""
    w.timeline._set_hover("c")
    assert w.timeline.hovered_view_id is None
    assert w.views["c"].property("trackHover") == ""


def test_unloading_track_clears_track_and_pane_hover(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w.timeline.hovered_view.connect(w._on_track_hovered)
    w.timeline.set_durations({"a": 10.0, "b": 10.0, "c": 10.0})
    w.timeline._set_hover("c")
    assert w.timeline.hovered_view_id == "c"
    assert w.views["c"].property("trackHover") == "true"
    w.timeline.set_durations({"c": None})
    assert w.timeline.hovered_view_id is None
    assert w.views["c"].property("trackHover") == ""


def test_clear_button_visibility_and_signal(qapp: QApplication) -> None:
    view = VideoView("C")
    seen: list[str] = []
    view.clear_requested.connect(seen.append)
    assert view.btn_clear.isHidden()
    assert not view.shows_surface
    view.set_loaded_name("c.mp4")
    assert view.shows_surface
    assert not view.btn_clear.isHidden()
    view.set_time_text("1.000s")
    view._zoom_label.setText("×2.0")
    view._rotation_label.setText("90°")
    view.btn_clear.click()
    assert seen == ["c"]
    view.clear_loaded()
    assert view.btn_clear.isHidden()
    assert not view.shows_surface
    assert view._caption.text() == "no video"
    assert view._time_label.text() == "--"
    assert view._zoom_label.text() == "×1.0"
    assert view._rotation_label.text() == ""
    assert view._stack.currentWidget() is view._placeholder


def test_clear_button_accepts_real_mouse_click(qapp: QApplication) -> None:
    view = VideoView("C")
    seen: list[str] = []
    view.clear_requested.connect(seen.append)
    view.set_loaded_name("c.mp4")
    view.show()
    QTest.mouseClick(view.btn_clear, Qt.MouseButton.LeftButton)
    assert seen == ["c"]
    view.close()


def test_main_window_clear_button_click_unloads(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._wire_view(w.views["c"], "c")
    p = _players(w)
    p["c"].duration = 10.0
    w.views["c"].set_loaded_name("c.mp4")
    QTest.mouseClick(w.views["c"].btn_clear, Qt.MouseButton.LeftButton)
    assert p["c"].unloads == 1
    assert w.views["c"]._caption.text() == "no video"


def test_clear_one_view_unloads_and_resets(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
        player.meta = object()
    for view in w.views.values():
        view.set_loaded_name("video.mp4")
    w.timeline.set_durations({vid: 10.0 for vid in w.view_ids})
    w._clear_view_video("c")
    assert p["c"].unloads == 1
    assert p["c"].duration is None
    assert w.views["c"]._caption.text() == "no video"
    assert w.timeline._dur["a"] == 10.0
    assert w.timeline._dur["c"] is None
    assert "cleared" in w.statusBar().currentMessage()


def test_clearing_master_promotes_first_loaded_follower(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
        player.meta = object()
    for view in w.views.values():
        view.set_loaded_name("video.mp4")
    w._clear_view_video("a")
    assert p["a"].unloads == 1
    assert w._session.master == "b"
    assert "b" not in w._session.engines
    assert w._session.engines.keys() == {"a", "c"}
    assert "reference" in w.statusBar().currentMessage()


def test_clear_works_when_duration_is_missing_but_pane_shows_video(
    qapp: QApplication,
) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    p["c"].duration = None
    w.views["c"].set_loaded_name("c.mp4")
    w._clear_view_video("c")
    assert p["c"].unloads == 1
    assert w.views["c"]._caption.text() == "no video"


def test_recording_blocks_clearing_videos(qapp: QApplication) -> None:
    class FakeRecorder:
        recording = True

    w = make_window(qapp, n=2)
    w._rec_controller._recorder = FakeRecorder()  # type: ignore[assignment]
    w._refresh_view_buttons()
    p = _players(w)
    p["a"].duration = 10.0
    assert not w._clear_all_action.isEnabled()
    assert not w.btn_add.isEnabled()
    assert not w.btn_remove.isEnabled()
    assert all(not view.btn_clear.isEnabled() for view in w.views.values())
    w._clear_view_video("a")
    assert p["a"].unloads == 0
    assert "Stop recording" in w.statusBar().currentMessage()
    w._clear_all_videos()
    assert p["a"].unloads == 0


def test_promoted_master_resets_speed_on_all_views(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
    w._session.set_master("b")
    w._transport.reset_speeds()
    assert p["a"].speeds[-1] == w.config.speed
    assert p["b"].speeds[-1] == w.config.speed
    assert p["c"].speeds[-1] == w.config.speed


def test_sync_chip_uses_promoted_reference_label(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._session.set_master("b")
    w._set_sync_chip({"a": 0.04, "c": -0.12})
    assert "BA +0.040" in w.sync_chip.text()
    assert "BC -0.120" in w.sync_chip.text()


def test_removing_promoted_reference_promotes_survivor(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
    for view in w.views.values():
        view.set_loaded_name("video.mp4")
    w._clear_view_video("a")  # B is promoted
    w._clear_view_video("b")  # C is promoted
    p["b"].duration = 10.0
    w.views["b"].set_loaded_name("b.mp4")
    w._remove_last_view()  # removes C, the current reference
    assert w._session.master == "b"
    assert "c" not in w.views
    assert "c" not in w._session.engines
    w._on_ui_tick()  # must not raise KeyError


def test_load_progress_pushes_duration_before_ready(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["b"].duration = 10.0
    p["b"]._loading = True
    w._on_load_progress()
    assert w.timeline._dur["b"] == 10.0


def test_empty_reference_promotes_first_loaded_view(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["b"].duration = 10.0
    w.views["b"].set_loaded_name("b.mp4")
    w._on_load_progress()
    assert w._session.master == "b"
    assert set(w._session.engines) == {"a"}


def test_clear_all_videos_unloads_everything(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    for player in p.values():
        player.duration = 10.0
    for view in w.views.values():
        view.set_loaded_name("video.mp4")
    w._session.set_master("b")
    w._clear_all_videos()
    assert all(player.unloads == 1 for player in p.values())
    assert all(view._caption.text() == "no video" for view in w.views.values())
    assert all(dur is None for dur in w.timeline._dur.values())
    assert w.sync_chip.text() == "● not synced"
    assert w._session.master == "a"
    assert "a" not in w._session.engines
    assert "All videos cleared" in w.statusBar().currentMessage()


def test_view_set_owns_order_and_active(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    assert w.view_ids == ["a", "b", "c"]
    w._set_active_view("b")
    assert w._active_view == "b"
    assert w.views["b"].property("activePane") == "true"
    assert w.views["a"].property("activePane") == ""
    w._remove_last_view()  # active b survives, c is gone
    assert w.view_ids == ["a", "b"]
    assert w._active_view == "b"


def test_empty_a_still_updates_followers(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].time_pos = None
    p["b"].time_pos = 5.0
    p["b"].is_playing = True
    w._on_ui_tick()
    assert w.views["b"]._time_label.text() == "5.000s"
    assert w._status_live.text() == "A not loaded"


def test_initialize_wires_views_and_engines(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    monkeypatch.setattr("vcplayer.ui.main_window.rec.warm_up_probe", lambda: None)
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    w = MainWindow(AppConfig())
    w.initialize([])
    assert w.view_ids == ["a", "b"]
    assert "b" in w._session.engines
    assert w.timeline._views == ["a", "b"]
    assert w._initialized
    w.close()


def test_close_clears_pending_loads(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    w = make_window(qapp, n=2)
    w._loads.start("b", Path("x.mp4"), _players(w)["b"])
    w.close()
    assert w._loads.pending_ids == set()
