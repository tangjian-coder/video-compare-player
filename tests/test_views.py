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
from PySide6.QtWidgets import QApplication

from vcplayer.config import AppConfig
from vcplayer.core.sync import SyncEngine
from vcplayer.ui import layout as pane_layout
from vcplayer.ui.main_window import MainWindow
from vcplayer.ui.video_view import VideoView


class FakePlayer:
    """Just enough PlayerController surface for the window code paths."""

    def __init__(self) -> None:
        self.is_playing = False
        self.meta: Any = None
        self.duration: float | None = None
        self.time_pos: float | None = 0.0
        self.fps: float | None = 60.0
        self.playback_time: float | None = None
        self.seeks: list[float] = []
        self.fast_seeks: list[float] = []
        self.speeds: list[float] = []
        self.plays = 0

    def play(self) -> None:
        self.plays += 1
        self.is_playing = True

    def pause(self) -> None:
        self.is_playing = False

    def terminate(self) -> None: ...

    def set_speed(self, speed: float) -> None:
        self.speeds.append(speed)

    def set_crosshair(self, enabled: bool) -> None: ...

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
        w.views[vid] = view
        w.view_host.add_view(view)
        w._layout = pane_layout.add_view(w._layout, vid, prior)
        w.view_host.apply_layout(w._layout)
        w.view_ids.append(vid)  # type: ignore[arg-type]
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
    assert "c" not in w.engines
    assert "c" not in w._load_pending
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
    for _vid, btn in w2._open_btns.items():
        assert btn.isVisibleTo(w2) is True
    w3 = make_window(qapp, n=3)
    assert w3._open_btns["a"].isVisibleTo(w3)
    assert not w3._open_btns["d"].isVisibleTo(w3)


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
    w._recorder = FakeRecorder()  # type: ignore[assignment]
    w._on_view_dropped_on("d", "a", pane_layout.ZONE_LEFT)
    assert w._layout == [["a", "b"], ["c", "d"]]


def _players(w: MainWindow) -> dict[str, FakePlayer]:
    return {vid: view.surface._player for vid, view in w.views.items()}  # type: ignore[misc]


def test_seek_master_all_clamps_to_common_range(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["b"].duration, p["c"].duration = 100.0, 50.0, 80.0
    w.engines["b"] = SyncEngine(p["a"], p["b"])
    w.engines["c"] = SyncEngine(p["a"], p["c"])
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
    w.engines["b"] = SyncEngine(p["a"], p["b"])
    w.engines["c"] = SyncEngine(p["a"], p["c"])
    # Disjoint ranges: offset -6 puts A-C at (6, 8), A-B at (0, 5).
    w.engines["c"].state.point_a, w.engines["c"].state.point_b = 6.0, 0.0
    w._seek_master_all(1.0)
    assert p["a"].seeks == []
    assert p["b"].seeks == []
    assert p["c"].seeks == []


def test_remove_synced_view_refreshes_chip(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    w.engines["b"] = SyncEngine(p["a"], p["b"])
    w.engines["c"] = SyncEngine(p["a"], p["c"])
    for vid in ("b", "c"):
        w.engines[vid].state.point_a = 1.0
        w.engines[vid].state.point_b = 2.0
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
    w.engines["b"] = SyncEngine(p["a"], p["b"])
    w._toggle_play()
    assert p["a"].plays == 1
    assert p["b"].plays == 1
    assert p["c"].plays == 0  # must not start from 0 once the file opens


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


def test_loaded_view_joins_running_synced_playback(qapp: QApplication) -> None:
    from pathlib import Path

    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["b"].duration, p["c"].duration = 100.0, 100.0, 100.0
    p["a"].time_pos = 12.0
    w.engines["c"] = SyncEngine(p["a"], p["c"])
    w.engines["c"].state.point_a = 1.0
    w.engines["c"].state.point_b = 2.0  # offset +1
    w._playing = True
    w._on_view_loaded("c", Path("x.mp4"))
    assert p["c"].seeks == [13.0]  # master 12 + offset 1
    assert p["c"].plays == 1


def test_loaded_view_stays_paused_when_unsynced(qapp: QApplication) -> None:
    from pathlib import Path

    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["c"].duration = 100.0, 100.0
    p["a"].time_pos = 12.0
    w.engines["c"] = SyncEngine(p["a"], p["c"])  # no anchors yet
    w._playing = True
    w._on_view_loaded("c", Path("x.mp4"))
    assert p["c"].seeks == []
    assert p["c"].plays == 0


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
    w._recorder = FakeRecorder()  # type: ignore[assignment]
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
