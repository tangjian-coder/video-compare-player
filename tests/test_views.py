"""Offscreen GUI tests for dynamic view add/remove bookkeeping.

Runs the real MainWindow/VideoView widgets on Qt's offscreen platform with
stub players, exercising the ＋/－ bookkeeping that the live smoke test
cannot reach without mouse automation.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # before QApplication

from concurrent.futures import Future
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QMouseEvent, QPointingDevice
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMenu

from vcplayer.config import AppConfig
from vcplayer.core.player import PlayerError
from vcplayer.ui import layout as pane_layout
from vcplayer.ui.main_window import MainWindow
from vcplayer.ui.pane_drag import video_paths
from vcplayer.ui.timeline import (
    TRACK_H,
    TRACK_LEFT,
    TRACK_PITCH,
    TRACK_RIGHT,
    TRACK_TOP_Y,
    TimelineWidget,
)
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
        self.source_orientation: str | None = "landscape"
        self.display_dimensions: tuple[float, float] | None = None
        self.zoom = 1.0
        self.playback_time: float | None = None
        self.seeks: list[float] = []
        self.fast_seeks: list[float] = []
        self.speeds: list[float] = []
        self.plays = 0
        self.unloads = 0
        self.terminated = 0
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

    def terminate(self) -> None:
        self.terminated += 1

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

    def set_rotation(self, degrees: int) -> None:
        self.rotation = int(degrees) % 360

    def rotate_cw(self) -> None:
        self.set_rotation(self.rotation + 90)

    def seek_exact(self, t: float) -> None:
        self.seeks.append(t)
        self.time_pos = t

    def seek_fast(self, t: float) -> None:
        self.fast_seeks.append(t)
        self.time_pos = t


class _AbortFuture(Future[Any]):
    """Future with python-mpv's abort semantics: cancel never completes it."""

    def __init__(self) -> None:
        super().__init__()
        self.aborted = False

    def cancel(self) -> bool:
        if self.done():
            return False
        self.aborted = True
        return True


class SlowFakePlayer(FakePlayer):
    """Player whose async seeks stay pending until the test resolves them."""

    def __init__(self) -> None:
        super().__init__()
        self.fast_async: list[float] = []
        self.exact_async: list[float] = []
        self.pending: list[_AbortFuture] = []

    def _future(self) -> Future[Any]:
        future = _AbortFuture()
        self.pending.append(future)
        return future

    def seek_fast_async(self, t: float) -> Future[Any]:
        self.fast_async.append(t)
        return self._future()

    def seek_exact_async(self, t: float) -> Future[Any]:
        self.exact_async.append(t)
        return self._future()


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


def test_remove_to_single_and_refuses_below_one(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._remove_last_view()
    assert w.view_ids == ["a"]
    assert w.view_host.count == 1
    assert w.timeline._views == ["a"]
    assert w._session.engines == {}
    w._remove_last_view()
    assert w.view_ids == ["a"]


def test_add_view_refuses_before_initialize(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._initialized = False
    w._add_view()
    assert w.view_ids == ["a", "b"]


def test_view_button_gating(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    assert w.btn_add.isEnabled() and w.btn_remove.isEnabled()
    assert "unloads its video" in w.btn_remove.toolTip()
    w._remove_last_view()
    assert w.btn_add.isEnabled() and not w.btn_remove.isEnabled()
    assert not w._open_actions["b"].isEnabled()
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


def test_single_layout_action_keeps_only_a_and_transport_works(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=4)
    w._session.rebuild()
    p = _load_only(w, "a")
    assert w._set_layout([["a", "b", "c", "d"]])
    assert w._layout_customized
    removed: list[str] = []
    original = w._destroy_view

    def spy(vid: str, *, replacement: str | None = None) -> None:
        removed.append(vid)
        original(vid, replacement=replacement)  # type: ignore[arg-type]

    monkeypatch.setattr(w, "_destroy_view", spy)
    menu = QMenu()
    w._populate_layout_menu(menu)
    action = next(a for a in menu.actions() if a.text() == "Single (close others)")
    assert not action.isCheckable()
    assert menu.toolTipsVisible()
    assert "keep only A" in action.toolTip()
    action.trigger()
    assert removed == ["d", "c", "b"]
    assert all(p[vid].terminated == 1 for vid in ("b", "c", "d"))

    assert w.view_ids == ["a"]
    assert w._layout == [["a"]]
    assert not w._layout_customized
    assert "closed 3 views" in w.statusBar().currentMessage()
    assert w.view_host.count == 1
    assert w.timeline._views == ["a"]
    assert w._session.engines == {}
    assert not w.btn_remove.isEnabled()
    assert w.btn_add.isEnabled()
    assert all(not w._open_actions[vid].isEnabled() for vid in ("b", "c", "d"))

    w._toggle_play()
    assert w._transport.playing
    assert p["a"].plays == 1
    w._on_seek_committed(3.0)
    assert p["a"].seeks == [3.0]

    p["a"].seeks.clear()
    w._step(1)
    assert p["a"].seeks == [pytest.approx(3.0 + 1.0 / 60.0)]
    w._goto_last()
    assert p["a"].seeks[-1] == pytest.approx(10.0 - 1.0 / 60.0)
    w._goto_first()
    assert p["a"].seeks[-1] == 0.0

    assert not w.btn_layout.isEnabled()
    assert "Add another view" in w.btn_layout.toolTip()
    single_menu = QMenu()
    w._populate_layout_menu(single_menu)
    assert single_menu.actions() == []
    menu.deleteLater()
    single_menu.deleteLater()


def test_single_view_add_restores_default_dual_layout(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    w = make_window(qapp, n=2)
    w._set_single_view()
    w._add_view()
    assert w.view_ids == ["a", "b"]
    assert w._layout == [["a", "b"]]
    assert w.btn_remove.isEnabled()
    assert w._open_actions["b"].isEnabled()


def test_manual_remove_to_single_resets_custom_layout(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    w = make_window(qapp, n=4)
    assert w._set_layout([["a", "b", "c", "d"]])
    assert w._layout_customized
    for _ in range(3):
        w._remove_last_view()
    assert w.view_ids == ["a"]
    assert w._layout == [["a"]]
    assert not w._layout_customized
    for _ in range(3):
        w._add_view()
    assert w._layout == [["a", "b"], ["c", "d"]]


def test_single_align_asks_for_a_second_view(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    w._set_single_view()
    w._align_current_frames()
    assert w.statusBar().currentMessage() == "Add a second view to align"


def test_single_align_without_video_asks_for_a_video(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._set_single_view()
    w._align_current_frames()
    assert w.statusBar().currentMessage() == "Load a video first"


def test_missing_source_pauses_and_blocks_session(qapp: QApplication) -> None:
    from pathlib import Path

    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["b"].duration = 10.0
    p["b"].meta = object()
    p["b"].time_pos = 0.0
    w.views["b"].set_loaded_name("b.mp4")
    source = Path("clip.mp4")
    w._source_monitor.track("a", source)
    p["a"].is_playing = True
    w._transport.playing = True

    w._on_source_missing("a", source)
    assert w.views["a"].source_missing
    assert "missing" in w.views["a"]._caption.text()
    assert not p["a"].is_playing
    assert not w._transport.playing
    assert not w.views["a"].btn_play.isEnabled()
    assert not w.views["b"].btn_play.isEnabled()
    assert not w.views["b"].btn_step_fwd.isEnabled()
    assert "deleted or moved" in w.statusBar().currentMessage()

    w._toggle_play()
    assert w.statusBar().currentMessage() == "Reload or clear the missing source first"
    w._toggle_play_view("b")
    assert not p["b"].is_playing
    assert w.statusBar().currentMessage() == "Reload or clear the missing source first"
    w._step_view("b", 1)
    assert w.statusBar().currentMessage() == "Reload or clear the missing source first"
    w._align_current_frames()
    assert w.statusBar().currentMessage() == "Reload or clear the missing source first"

    w._clear_view_video("a")
    assert not w.views["a"].source_missing
    assert w.views["a"].btn_play.isEnabled()
    assert w.views["b"].btn_play.isEnabled()
    assert w.views["b"].btn_step_fwd.isEnabled()


def test_open_video_shows_loading_feedback(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"x")
    w.open_video("a", path)
    view = w.views["a"]
    assert view.loading
    assert view._stack.currentWidget() is view._loading_page
    assert view._loading_name.text() == "clip.mp4"
    assert not view.btn_play.isEnabled()
    assert "opening clip.mp4" in w.statusBar().currentMessage()

    w._on_video_loaded("a", path, object())
    assert not view.loading
    assert view._stack.currentWidget() is view.surface
    assert view.btn_play.isEnabled()
    w._loads.clear()
    w._source_monitor.clear()


def test_video_paths_preserves_order_and_filters(tmp_path: Path) -> None:
    mp4 = tmp_path / "first.mp4"
    mov = tmp_path / "second.MOV"
    txt = tmp_path / "notes.txt"
    mime = QMimeData()
    mime.setUrls(
        [
            QUrl.fromLocalFile(str(txt)),
            QUrl.fromLocalFile(str(mp4)),
            QUrl.fromLocalFile(str(mov)),
        ]
    )
    assert video_paths(mime) == [mp4, mov]


def test_video_drop_forwards_all_paths(qapp: QApplication, tmp_path: Path) -> None:
    view = VideoView("A")
    seen: list[list[Path]] = []
    view.files_dropped.connect(seen.append)
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mkv"
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(first)), QUrl.fromLocalFile(str(second))])
    event = QDropEvent(
        QPointF(10.0, 10.0),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    view.dropEvent(event)
    view.surface.dropEvent(event)
    assert seen == [[first, second], [first, second]]


def test_single_drop_keeps_existing_view_count(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    clip = tmp_path / "single.mp4"
    clip.write_bytes(b"x")
    w._on_files_dropped("b", [clip])
    assert w.view_ids == ["a", "b"]
    assert w.views["b"].loading
    assert w.views["b"]._loading_name.text() == "single.mp4"
    assert not w.views["a"].loading
    w._loads.clear()


def test_multi_drop_auto_adds_and_wraps_from_target(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    w = make_window(qapp, n=2)
    paths = [tmp_path / f"{name}.mp4" for name in ("one", "two", "three", "four")]
    for path in paths:
        path.write_bytes(b"x")

    w._on_files_dropped("b", paths)
    assert w.view_ids == ["a", "b", "c", "d"]
    assert w.views["b"]._loading_name.text() == "one.mp4"
    assert w.views["c"]._loading_name.text() == "two.mp4"
    assert w.views["d"]._loading_name.text() == "three.mp4"
    assert w.views["a"]._loading_name.text() == "four.mp4"
    assert "Added 2 view(s)" in w.statusBar().currentMessage()
    assert "Loading 4 videos: B, C, D, A" in w.statusBar().currentMessage()
    w._loads.clear()


def test_multi_drop_replaces_from_target_and_skips_extras(
    qapp: QApplication, tmp_path: Path
) -> None:
    w = make_window(qapp, n=4)
    paths = [tmp_path / f"{i}.mp4" for i in range(6)]
    for path in paths:
        path.write_bytes(b"x")

    w._on_files_dropped("c", paths)
    assert w.views["c"]._loading_name.text() == "0.mp4"
    assert w.views["d"]._loading_name.text() == "1.mp4"
    assert w.views["a"]._loading_name.text() == "2.mp4"
    assert w.views["b"]._loading_name.text() == "3.mp4"
    assert "(skipped 2)" in w.statusBar().currentMessage()
    w._loads.clear()


def test_multi_drop_is_blocked_while_recording(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=2)
    monkeypatch.setattr(w, "_recording", lambda: True)
    paths = [tmp_path / f"{i}.mp4" for i in range(2)]
    for path in paths:
        path.write_bytes(b"x")

    w._on_files_dropped("a", paths)
    assert w.view_ids == ["a", "b"]
    assert not w.views["a"].loading
    assert w.statusBar().currentMessage() == "Stop recording before opening videos"


def test_placeholder_pages_are_parented(qapp: QApplication) -> None:
    view = VideoView("A")
    assert view._placeholder.parent() is view
    assert view._loading_page.parent() is view


def test_async_seek_releases_timeline_preview(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")

    w._on_scrub_preview(2.0)
    assert w.timeline._t["a"] == pytest.approx(2.0)
    w.timeline.set_playheads({"a": 0.0})
    assert w.timeline._t["a"] == pytest.approx(2.0)

    w._on_seek_committed(2.0)
    assert w._transport.exact_seek_pending
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending
    assert w.timeline._t["a"] == pytest.approx(2.0)


def test_preview_coalesces_and_exact_wins(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.duration = 10.0
    player.meta = object()
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_scrub_preview(2.0)
    w._on_scrub_preview(3.0)
    assert player.fast_async == [2.0]
    assert w.timeline._t["a"] == pytest.approx(3.0)

    player.pending.pop(0).set_result(None)
    w._on_ui_tick()
    assert player.fast_async == [2.0, 3.0]

    w._on_seek_committed(4.0)
    assert player.exact_async == [4.0]
    assert player.seeks == []
    assert player.pending.pop(0).aborted
    player.time_pos = 4.0
    player.pending.pop(0).set_result(None)
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending
    assert not w.timeline._preview_hold


def test_solo_playback_keeps_fast_ui_rate(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["b"].meta = object()
    p["b"].duration = 10.0
    p["b"].time_pos = 0.0
    w._update_ui_rate()
    assert w._ui_timer.interval() == 100

    w._toggle_play_view("b")
    assert p["b"].is_playing
    assert w._ui_timer.interval() == 16
    w._toggle_play_view("b")
    assert w._ui_timer.interval() == 100


def test_align_waits_for_pending_exact_seek(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(4.0)
    assert w._transport.exact_seek_pending
    w._align_current_frames()
    assert "Wait for the current seek" in w.statusBar().currentMessage()
    assert player.exact_async == [4.0]
    player.time_pos = 4.0
    player.pending.pop(0).set_result(None)
    w._on_ui_tick()


def test_seek_timeout_invalidates_late_completion(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(4.0)
    old_token = w._transport._commit_token
    w._transport._commit_started -= 3.0
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending
    assert player.pending.pop(0).aborted
    assert "timed out" in w.statusBar().currentMessage()

    # Deliver the stale completion explicitly; token invalidation must drop it.
    player.time_pos = 4.0
    w._transport._seek_completions.put(("exact", old_token, None))
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending
    assert w.statusBar().currentMessage() == "Seek timed out; try again"


def test_seek_target_is_clamped_before_virtual_step(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(20.0)
    assert player.exact_async == [10.0]
    assert w._transport._commit_target == pytest.approx(10.0)
    player.time_pos = 10.0
    player.pending.pop(0).set_result(None)
    w._on_ui_tick()


def test_single_frame_step_waits_for_position_change(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._step(1)
    assert player.exact_async == [pytest.approx(1.0 / 60.0)]
    player.pending.pop(0).set_result(None)
    w._on_ui_tick()
    assert w._transport.exact_seek_pending

    player.time_pos = 1.0 / 60.0
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending


def test_exact_seek_waits_for_all_views_to_land(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    slow: list[SlowFakePlayer] = []
    for vid in ("a", "b"):
        player = SlowFakePlayer()
        player.meta = object()
        player.duration = 100.0
        player.time_pos = 0.0
        w.views[vid].surface._player = player  # type: ignore[assignment]
        slow.append(player)
    w._session.rebuild()

    w._on_seek_committed(10.0)
    assert [player.exact_async for player in slow] == [[10.0], [10.0]]
    for player in slow:
        player.pending.pop(0).set_result(None)
    slow[0].time_pos = 10.0
    slow[1].time_pos = 0.0
    w._on_ui_tick()
    assert w._transport.exact_seek_pending

    slow[1].time_pos = 10.0
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending


def test_play_waits_for_pending_exact_seek(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(4.0)
    w._toggle_play()
    assert not w._transport.playing
    assert w._transport._play_after_seek
    player.time_pos = 4.0
    player.pending.pop(0).set_result(None)
    w._on_ui_tick()
    assert w._transport.playing
    assert player.is_playing


def test_play_cancels_pending_preview(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_scrub_preview(2.0)
    assert w.timeline.preview_hold
    preview = player.pending.pop(0)
    w._toggle_play()
    assert preview.aborted
    assert w._transport.playing
    assert not w._transport.preview_pending
    w._on_ui_tick()
    assert player.fast_async == [2.0]
    assert not w.timeline.preview_hold


def test_second_play_press_cancels_play_after_seek(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(4.0)
    w._toggle_play()
    assert w._transport._play_after_seek
    w._toggle_play()
    assert not w._transport._play_after_seek

    player.time_pos = 4.0
    player.pending.pop(0).set_result(None)
    w._on_ui_tick()
    assert not w._transport.playing
    assert not player.is_playing


def test_preview_hold_has_watchdog(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")

    w.timeline.preview_master(2.0)
    assert w.timeline.preview_hold
    w.timeline._last_preview_at -= 2.0
    w._on_ui_tick()
    assert not w.timeline.preview_hold


def test_stale_preview_completion_keeps_new_target(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_scrub_preview(2.0)
    old = player.pending.pop(0)
    w._on_scrub_preview(3.0)
    w._transport._preview_started -= 3.0
    w._on_ui_tick()
    assert old.aborted
    assert w._transport.preview_pending
    assert w._transport._preview_target == pytest.approx(3.0)

    w._on_ui_tick()  # deliver the stale completion
    assert w._transport._preview_target == pytest.approx(3.0)
    w._transport.cancel_pending_seeks()
    w.timeline.release_preview()
    w._loads.clear()
    w._source_monitor.clear()


def test_clear_sync_cancels_pending_seek(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(4.0)
    exact = player.pending.pop(0)
    w.timeline.preview_master(4.0)
    assert w._transport.exact_seek_pending
    assert w.timeline.preview_hold

    w._clear_sync()
    assert exact.aborted
    assert not w._transport.exact_seek_pending
    assert not w.timeline.preview_hold
    assert w.statusBar().currentMessage() == "Sync cleared"
    w._on_ui_tick()
    assert not w._transport.exact_seek_pending


def test_loading_follower_keeps_time_label_blank(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    path = tmp_path / "loading.mp4"
    path.write_bytes(b"x")
    w.open_video("b", path)
    assert w.views["b"].loading

    w._on_ui_tick()
    assert w.views["b"]._time_label.text() == "--"
    w._loads.clear()
    w._source_monitor.clear()


def test_reload_clears_timeline_duration_immediately(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "b")
    assert w.timeline._dur["b"] == pytest.approx(10.0)
    path = tmp_path / "reload.mp4"
    path.write_bytes(b"x")
    w.open_video("b", path)
    assert w.views["b"].loading
    assert w.timeline._dur["b"] is None
    w._loads.clear()
    w._source_monitor.clear()


def test_failed_load_returns_to_placeholder(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("vcplayer.ui.main_window.QMessageBox.warning", lambda *a, **k: None)
    w = make_window(qapp, n=2)
    path = tmp_path / "bad.mp4"
    path.write_bytes(b"x")
    w.open_video("a", path)
    w._on_load_failed("a", path, "bad file")
    view = w.views["a"]
    assert not view.loading
    assert view._stack.currentWidget() is view._placeholder
    assert view.btn_play.isEnabled()
    assert "load failed" in w.statusBar().currentMessage()
    w._load_failures.clear()
    w._showing_load_failure = False
    w._loads.clear()
    w._source_monitor.clear()


def test_new_preview_cancels_pending_exact(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    player = SlowFakePlayer()
    player.meta = object()
    player.duration = 10.0
    player.time_pos = 0.0
    w.views["a"].surface._player = player  # type: ignore[assignment]

    w._on_seek_committed(4.0)
    exact_future = player.pending.pop(0)
    assert not exact_future.aborted
    w._on_scrub_preview(2.0)
    assert exact_future.aborted
    assert not w._transport.exact_seek_pending
    assert player.fast_async == [2.0]
    assert w.timeline._t["a"] == pytest.approx(2.0)
    w._transport.cancel_pending_seeks()
    w.timeline.release_preview()
    w._loads.clear()
    w._source_monitor.clear()


def test_loading_status_survives_reference_promotion(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    path = tmp_path / "opening.mp4"
    path.write_bytes(b"x")
    w.open_video("b", path)
    w._on_load_progress()
    assert w._session.master == "b"
    assert "opening opening.mp4" in w.statusBar().currentMessage()
    w._on_ui_tick()
    assert w._status_live.text().startswith("B: opening")
    w._loads.clear()
    w._source_monitor.clear()


def test_stale_missing_event_is_ignored(qapp: QApplication) -> None:
    from pathlib import Path

    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    w._source_monitor.track("a", Path("new.mp4"))
    w._on_source_missing("a", Path("old.mp4"))
    assert not w.views["a"].source_missing


def test_single_view_hides_layout_controls(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._set_single_view()
    assert not w.btn_layout.isEnabled()
    assert "Add another view" in w.btn_layout.toolTip()
    menu = QMenu()
    w._populate_layout_menu(menu)
    assert menu.actions() == []
    menu.deleteLater()


@pytest.mark.parametrize(
    ("n", "labels"),
    [
        (2, ["Side by side", "Stacked"]),
        (3, ["Row (1x3)", "Top 2 + 1", "Stacked (3 rows)"]),
        (4, ["Grid (2x2)", "Row (1x4)", "Top 3 + 1"]),
    ],
)
def test_layout_menu_lists_only_current_presets(
    qapp: QApplication, n: int, labels: list[str]
) -> None:
    w = make_window(qapp, n=n)
    assert w.btn_layout.isEnabled()
    menu = QMenu()
    w._populate_layout_menu(menu)
    actions = [a for a in menu.actions() if not a.isSeparator()]
    assert [a.text() for a in actions] == ["Reset layout", "Single (close others)", *labels]
    assert next(a for a in actions if a.text() == labels[0]).isChecked()
    menu.deleteLater()


def test_single_with_empty_a_destroys_the_only_loaded_video(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "b")
    assert w._session.master == "b"
    w._set_single_view()
    assert w.view_ids == ["a"]
    assert w._session.master == "a"
    assert p["b"].terminated == 1
    assert w._transport.playable_range() is None


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


def _load_only(w: MainWindow, vid: str, duration: float = 10.0) -> dict[str, FakePlayer]:
    """Make exactly one stub view ready and promote it as the reference."""
    p = _players(w)
    p[vid].duration = duration
    p[vid].meta = object()
    p[vid].time_pos = 0.0
    w.views[vid].set_loaded_name(f"{vid}.mp4")
    w._push_timeline_durations()
    w._ensure_reference()
    return p


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


def test_single_loaded_view_transport_works_for_all_presets(qapp: QApplication) -> None:
    for n in (2, 3, 4):
        w = make_window(qapp, n=n)
        w._session.rebuild()
        p = _load_only(w, "a")
        assert w._session.master == "a"
        for _label, preset in pane_layout.presets(w.view_ids):
            w._set_layout(preset)
            assert w._layout == preset
            assert w.view_host.count == n
            for player in p.values():
                player.plays = 0
                player.is_playing = False
                player.seeks.clear()
                player.fast_seeks.clear()

            w._toggle_play()
            assert w._transport.playing
            assert p["a"].plays == 1
            assert all(p[vid].plays == 0 for vid in w.view_ids if vid != "a")

            w._toggle_play()  # global pause must stop the sole playing view
            assert not w._transport.playing
            assert not p["a"].is_playing

            w._on_seek_committed(4.0)
            assert p["a"].seeks == [4.0]
            assert all(p[vid].seeks == [] for vid in w.view_ids if vid != "a")

            p["a"].seeks.clear()
            w._on_scrub_preview(2.0)
            assert p["a"].fast_seeks == [2.0]

            p["a"].seeks.clear()
            w._step(1)
            assert p["a"].seeks == [pytest.approx(2.0 + 1.0 / 60.0)]
            w._goto_last()
            assert p["a"].seeks[-1] == pytest.approx(10.0 - 1.0 / 60.0)
            w._goto_first()
            assert p["a"].seeks[-1] == 0.0
            w._on_ui_tick()


@pytest.mark.parametrize(("n", "vid"), [(2, "b"), (3, "c"), (4, "d")])
def test_single_loaded_follower_is_promoted_and_playable(
    qapp: QApplication, n: int, vid: str
) -> None:
    w = make_window(qapp, n=n)
    w._session.rebuild()
    p = _load_only(w, vid)
    assert w._session.master == vid
    w._toggle_play()
    assert w._transport.playing
    assert p[vid].plays == 1
    assert all(p[other].plays == 0 for other in w.view_ids if other != vid)
    w._on_seek_committed(3.0)
    assert p[vid].seeks == [3.0]
    assert all(p[other].seeks == [] for other in w.view_ids if other != vid)


def test_no_loaded_videos_keep_transport_inert(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._session.rebuild()
    p = _players(w)
    w._toggle_play()
    assert not w._transport.playing
    assert w.statusBar().currentMessage() == "Load a video first"
    w._on_seek_committed(1.0)
    w._step(1)
    w._goto_first()
    assert all(player.seeks == [] for player in p.values())


def test_single_solo_playing_view_is_paused_by_global_control(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["a"].is_playing = True
    assert not w._transport.playing
    w._toggle_play()
    assert not p["a"].is_playing
    assert p["a"].plays == 0


def test_single_view_restarts_at_zero_after_eof(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["a"].time_pos = 10.0
    p["a"].is_playing = True
    w._transport.playing = True
    w._on_ui_tick()
    assert not w._transport.playing
    assert not p["a"].is_playing
    w._toggle_play()
    w._on_ui_tick()
    assert p["a"].seeks == [0.0]
    assert p["a"].is_playing


def test_pane_pause_during_ensemble_pauses_instead_of_resuming(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["b"].meta = object()
    p["b"].duration = 10.0
    w._session.rebuild()
    p["a"].is_playing = p["b"].is_playing = True
    w._transport.playing = True
    w._toggle_play_view("a")
    assert not w._transport.playing
    assert not p["a"].is_playing
    assert not p["b"].is_playing


def test_default_preset_clears_custom_layout_flag(qapp: QApplication) -> None:
    w = make_window(qapp, n=4)
    assert w._set_layout([["a", "b", "c", "d"]])
    # Default geometry with a stale custom flag: the equality branch must
    # clear the flag instead of returning without touching it.
    assert w._set_layout([["a", "b"], ["c", "d"]], customized=True)
    assert w._layout_customized
    menu = QMenu()
    w._populate_layout_menu(menu)
    action = next(a for a in menu.actions() if a.text() == "Grid (2x2)")
    action.trigger()
    assert w._layout == [["a", "b"], ["c", "d"]]
    assert not w._layout_customized
    w._remove_last_view()
    assert w._layout == [["a", "b", "c"]]
    menu.deleteLater()


def test_ready_but_unpainted_master_is_not_demoted(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._session.rebuild()
    p = _players(w)
    p["a"].meta = object()
    p["a"].duration = 10.0
    p["b"]._loading = True
    w._on_load_progress()
    assert w._session.master == "a"

    p["b"]._loading = False
    p["c"]._loading = True
    w._on_load_progress()
    assert w._session.master == "a"


def test_single_loaded_timeline_click_seeks_master(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    w._session.rebuild()
    p = _load_only(w, "a")
    w._set_single_view()
    assert w.timeline._views == ["a"]
    w.timeline.scrub_preview.connect(w._on_scrub_preview)
    w.timeline.seek_committed.connect(w._on_seek_committed)
    w.timeline.resize(600, w.timeline.minimumHeight())
    assert w.timeline._is_loaded("a")
    assert w.timeline._span == 10.0

    usable = w.timeline.width() - TRACK_LEFT - TRACK_RIGHT
    x = TRACK_LEFT + usable / 2.0
    y = TRACK_TOP_Y + TRACK_H / 2.0
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        QPointingDevice.primaryPointingDevice(),
    )
    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(x, y),
        QPointF(x, y),
        QPointF(x, y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        QPointingDevice.primaryPointingDevice(),
    )
    w.timeline.mousePressEvent(press)
    w.timeline.mouseReleaseEvent(release)
    assert p["a"].fast_seeks[-1] == pytest.approx(5.0)
    assert p["a"].seeks[-1] == pytest.approx(5.0)


def test_solo_view_at_eof_restarts_on_first_global_play(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["a"].time_pos = 10.0
    p["a"].is_playing = True
    assert not w._transport.playing
    w._toggle_play()
    w._on_ui_tick()
    assert p["a"].seeks == [0.0]
    assert p["a"].is_playing


def test_deferred_resync_does_not_steal_solo_playback(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["b"].meta = object()
    p["b"].duration = 10.0
    w.views["b"].set_loaded_name("b.mp4")
    w._session.rebuild()
    engine = w._session.engines["b"]
    engine.state.point_a = 0.0
    engine.state.point_b = 0.0
    p["a"].time_pos = 5.0
    p["b"].time_pos = 4.0
    p["b"].is_playing = True
    w._resync_if_needed()
    assert p["b"].seeks == []


def test_pause_resync_uses_synchronous_seek(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=2)
    calls: list[bool] = []
    monkeypatch.setattr(w._transport, "any_engine_ready", lambda: True)
    monkeypatch.setattr(
        w._session,
        "resync_ready",
        lambda *, async_seek=True: calls.append(async_seek),
    )

    w._transport.resync_if_needed()
    assert calls == [False]


def test_any_player_playing_tolerates_missing_player(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w.views["a"].surface._player = None
    assert w._transport.any_player_playing() is False


def test_single_solo_playback_shows_pause_icon(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["a"].is_playing = True
    w._on_ui_tick()
    assert "Pause" in w.btn_play.toolTip()


def test_solo_playback_pauses_while_reference_is_loading(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _players(w)
    p["a"]._loading = True
    p["b"].meta = object()
    p["b"].duration = 10.0
    p["b"].is_playing = True
    w.views["b"].set_loaded_name("b.mp4")
    w._on_ui_tick()
    assert "Pause" in w.btn_play.toolTip()
    w._toggle_play()
    assert not p["b"].is_playing


def test_single_solo_playback_at_eof_shows_play_icon(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    p = _load_only(w, "a")
    p["a"].time_pos = 10.0
    p["a"].is_playing = True
    w._on_ui_tick()
    assert "Pause" not in w.btn_play.toolTip()


def test_goto_without_common_range_reports_it(qapp: QApplication) -> None:
    w = make_window(qapp, n=3)
    p = _players(w)
    p["a"].duration, p["b"].duration, p["c"].duration = 100.0, 5.0, 2.0
    p["a"].meta = p["b"].meta = p["c"].meta = object()
    w._session.add_follower("b")
    w._session.add_follower("c")
    w._session.engines["c"].state.point_a = 6.0
    w._session.engines["c"].state.point_b = 0.0
    w._transport.goto_first()
    assert "No common range" in w.statusBar().currentMessage()


@pytest.mark.parametrize("n", [2, 3, 4])
def test_layout_presets_set_custom_flag_by_default_status(qapp: QApplication, n: int) -> None:
    w = make_window(qapp, n=n)
    default = pane_layout.default_layout(w.view_ids)
    menu = QMenu()
    w._populate_layout_menu(menu)
    for _label, preset in pane_layout.presets(w.view_ids):
        action = next(a for a in menu.actions() if a.text() == _label)
        action.trigger()
        assert w._layout == preset
        assert w._layout_customized == (preset != default)
    menu.deleteLater()


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
    w._set_single_view()
    assert w.view_ids == ["a", "b", "c", "d"]
    menu = QMenu()
    w._populate_layout_menu(menu)
    action = next(a for a in menu.actions() if a.text() == "Single (close others)")
    assert not action.isEnabled()
    menu.deleteLater()


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


def test_copy_button_copies_filename(qapp: QApplication) -> None:
    view = VideoView("A")
    seen: list[tuple[str, str]] = []
    view.name_copied.connect(lambda vid, name: seen.append((vid, name)))
    view.set_loaded_name("clip.mp4")
    assert not view.btn_copy.isHidden()

    QApplication.clipboard().setText("sentinel")
    view.btn_copy.click()
    assert QApplication.clipboard().text() == "clip.mp4"
    assert seen == [("a", "clip.mp4")]


def test_copy_button_ignores_missing_suffix_and_hides_on_clear(qapp: QApplication) -> None:
    view = VideoView("A")
    view.set_loaded_name("clip.mp4")
    view.set_source_missing(True)
    view.btn_copy.click()
    assert QApplication.clipboard().text() == "clip.mp4"

    view.clear_loaded()
    assert view.btn_copy.isHidden()
    QApplication.clipboard().setText("sentinel")
    view.btn_copy.click()
    assert QApplication.clipboard().text() == "sentinel"


def test_copy_button_is_available_while_loading(qapp: QApplication) -> None:
    view = VideoView("A")
    view.set_loading("opening.mp4")
    assert not view.btn_copy.isHidden()
    view.btn_copy.click()
    assert QApplication.clipboard().text() == "opening.mp4"


def test_copy_button_reports_to_status_bar(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    view = w.views["a"]
    view.name_copied.connect(w._on_name_copied)
    view.set_loaded_name("clip.mp4")
    view.btn_copy.click()
    assert w.statusBar().currentMessage() == "A: copied clip.mp4"


def test_auto_landscape_applies_to_loaded_views(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].meta = p["b"].meta = object()
    p["a"].duration = p["b"].duration = 10.0
    p["a"].source_orientation = "portrait"
    p["b"].source_orientation = "landscape"
    p["b"].rotation = 90
    monkeypatch.setattr(w.config, "save", lambda: None)

    w._on_auto_landscape_toggled(True)
    assert w.config.auto_landscape is True
    assert p["a"].rotation == 90
    assert p["b"].rotation == 0
    assert w.statusBar().currentMessage() == "Auto landscape on"

    w._on_auto_landscape_toggled(False)
    assert w.config.auto_landscape is False
    assert p["a"].rotation == 90
    assert p["b"].rotation == 0
    assert w.statusBar().currentMessage() == "Auto landscape off"


def test_loaded_portrait_rotates_when_auto_landscape_on(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].meta = object()
    p["a"].duration = 10.0
    p["a"].source_orientation = "portrait"
    w.config.auto_landscape = True
    clip = tmp_path / "portrait.mp4"
    clip.write_bytes(b"x")

    w._on_video_loaded("a", clip, object())
    assert p["a"].rotation == 90
    assert w.views["a"]._rotation_label.text() == "90°"
    w._source_monitor.clear()
    w._loads.clear()


def test_new_load_resets_inherited_rotation(qapp: QApplication, tmp_path: Path) -> None:
    w = make_window(qapp, n=2)
    p = _players(w)
    p["a"].rotation = 90
    clip = tmp_path / "landscape.mp4"
    clip.write_bytes(b"x")

    assert w.open_video("a", clip)
    assert p["a"].rotation == 0
    assert w.views["a"]._rotation_label.text() == ""
    w._loads.clear()


def test_display_size_uses_final_mpv_dimensions(qapp: QApplication) -> None:
    view = VideoView("A")
    player = FakePlayer()
    player.meta = object()
    player.rotation = 90
    player.display_dimensions = (1920.0, 1080.0)
    view.surface._player = player  # type: ignore[assignment]
    view.surface.resize(800, 600)

    assert view.surface._display_size() == pytest.approx((800.0, 450.0))


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


def test_load_progress_keeps_loading_duration_cleared(qapp: QApplication) -> None:
    w = make_window(qapp, n=2)
    w._session.rebuild()
    _load_only(w, "a")
    p = _players(w)
    p["b"].duration = 10.0
    w.views["b"].set_loading("b.mp4")
    w._on_load_progress()
    assert w.timeline._dur["b"] is None


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


def test_initialize_loads_a_and_b_before_extra_cli_views(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    monkeypatch.setattr("vcplayer.ui.main_window.rec.warm_up_probe", lambda: None)
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    w = MainWindow(AppConfig())
    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(w, "open_video", lambda view, path: calls.append(("open", view, path)))
    monkeypatch.setattr(w, "_add_view", lambda load=None: calls.append(("add", "", load)))
    paths = [Path(f"{name}.mp4") for name in "abcd"]
    w.initialize(paths)
    assert calls == [
        ("open", "a", paths[0]),
        ("open", "b", paths[1]),
        ("add", "", paths[2]),
        ("add", "", paths[3]),
    ]
    w.close()


def test_initialize_skips_none_extra_cli_views(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    def fake_ensure_player(view: VideoView) -> Any:
        if view.surface._player is None:
            view.surface._player = FakePlayer()
        return view.surface._player

    monkeypatch.setattr(VideoView, "ensure_player", fake_ensure_player)
    monkeypatch.setattr("vcplayer.ui.main_window.rec.warm_up_probe", lambda: None)
    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    w = MainWindow(AppConfig())
    calls: list[tuple[str, str, object]] = []
    monkeypatch.setattr(w, "open_video", lambda view, path: calls.append(("open", view, path)))
    monkeypatch.setattr(w, "_add_view", lambda load=None: calls.append(("add", "", load)))
    paths = [Path("a.mp4"), Path("b.mp4"), None, Path("d.mp4")]
    w.initialize(paths)  # type: ignore[list-item]
    assert calls == [
        ("open", "a", paths[0]),
        ("open", "b", paths[1]),
        ("add", "", paths[3]),
    ]
    w.close()


def test_close_clears_pending_loads(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    monkeypatch.setattr(AppConfig, "save", lambda self: None)
    w = make_window(qapp, n=2)
    w._loads.start("b", Path("x.mp4"), _players(w)["b"])
    w.close()
    assert w._loads.pending_ids == set()
