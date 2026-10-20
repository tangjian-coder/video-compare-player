"""Tests for reference selection and view removal policy."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from vcplayer.core.sync import ViewId
from vcplayer.ui.session_controller import SessionController
from vcplayer.ui.video_view import VideoView


class FakePlayer:
    duration = 10.0
    fps = 60.0
    time_pos = 0.0

    def __init__(self, ready: bool, loading: bool) -> None:
        self.is_ready = ready
        self.is_loading = loading

    def seek_exact(self, t: float) -> None: ...
    def seek_fast(self, t: float) -> None: ...
    def set_speed(self, speed: float) -> None: ...


class FakeView:
    def __init__(self, vid: ViewId, *, ready: bool, loading: bool = False) -> None:
        self.view_id = vid
        self.player = FakePlayer(ready, loading)
        self.surface = SimpleNamespace(has_player=True)
        self.shows_surface = ready
        self.ready = ready
        self.loading = loading


def _view(session: SessionController, vid: ViewId) -> FakeView:
    return cast(FakeView, session.views[vid])


def _session() -> SessionController:
    views = [
        FakeView("a", ready=False),
        FakeView("b", ready=True),
        FakeView("c", ready=False, loading=True),
    ]
    return SessionController(cast(list[VideoView], views))


def test_choose_reference_prefers_ready_then_available() -> None:
    session = _session()
    _view(session, "a").shows_surface = True
    assert session.choose_reference(exclude="a") == "b"
    assert session.choose_reference(exclude="b") == "a"


def test_ensure_reference_waits_for_loading_master() -> None:
    session = _session()
    a = _view(session, "a")
    a.player.is_loading = True
    assert session.ensure_reference() is None
    a.player.is_loading = False
    assert session.ensure_reference() == "b"
    assert session.master == "b"


def test_removing_master_promotes_replacement() -> None:
    session = _session()
    assert session.set_master("b")
    removed = session.pop_view("b", replacement="c")
    assert removed is not None
    assert session.master == "c"
    assert "b" not in session.views
    assert "b" not in session.engines
