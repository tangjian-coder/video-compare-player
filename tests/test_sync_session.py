"""Tests for the sync session's master/engine ownership."""

from __future__ import annotations

from types import SimpleNamespace

from vcplayer.core.sync import SyncEngine
from vcplayer.ui.sync_session import SyncSession


class FakePlayer:
    def __init__(self, duration: float = 10.0) -> None:
        self.duration = duration
        self.fps = 60.0
        self.is_ready = True
        self.time_pos = 0.0
        self.playback_time = None
        self.seeks: list[float] = []

    def seek_exact(self, t: float) -> None:
        self.seeks.append(t)

    def seek_fast(self, t: float) -> None:
        self.seeks.append(t)

    def set_speed(self, speed: float) -> None: ...


def make_session() -> tuple[SyncSession, FakePlayer, FakePlayer]:
    a, b = FakePlayer(), FakePlayer()
    surface = SimpleNamespace(has_player=True)
    views = {
        "a": SimpleNamespace(player=a, surface=surface),
        "b": SimpleNamespace(player=b, surface=surface),
    }
    session = SyncSession(lambda: views)  # type: ignore[arg-type]
    session.engines["b"] = SyncEngine(a, b)  # type: ignore[arg-type]
    return session, a, b


def test_master_is_explicit_and_anchors_follow_state() -> None:
    session, _a, _b = make_session()
    assert session.master == "a"
    session.engines["b"].state.point_a = 1.0
    session.engines["b"].state.point_b = 2.0
    assert session.current_anchors() == {"a": 1.0, "b": 2.0}
    assert session.current_offsets() == {"b": 1.0}


def test_invalidate_master_clears_every_pair() -> None:
    session, _a, _b = make_session()
    session.engines["b"].state.point_a = 1.0
    session.engines["b"].state.point_b = 2.0
    assert session.invalidate("a") == ["b"]
    assert session.current_offsets() == {}


def test_set_master_rebuilds_engines_around_new_reference() -> None:
    session, a, b = make_session()
    assert session.set_master("b")
    assert session.master == "b"
    assert set(session.engines) == {"a"}
    engine = session.engines["a"]
    assert engine.a is b and engine.b is a


def test_set_master_is_idempotent_and_validated() -> None:
    session, _a, _b = make_session()
    session.engines["b"].state.point_a = 1.0
    session.engines["b"].state.point_b = 2.0
    assert not session.set_master("a")
    assert session.engines["b"].state.is_complete
    assert not session.set_master("z")  # type: ignore[arg-type]
    assert session.master == "a"


def test_remove_view_drops_its_engine() -> None:
    session, _a, _b = make_session()
    session.remove_view("b")
    assert session.engines == {}


def test_seek_master_all_seeks_master_and_followers() -> None:
    session, a, b = make_session()
    assert session.seek_master_all(5.0)
    assert a.seeks == [5.0]
    assert b.seeks == [5.0]


def test_seek_master_all_seeks_master_without_followers() -> None:
    session, a, _b = make_session()
    session.engines.clear()
    assert session.playable_range() == (0.0, 10.0)
    assert session.seek_master_all(5.0)
    assert a.seeks == [5.0]


def test_seek_master_all_reports_no_range_when_master_is_unready() -> None:
    session, a, _b = make_session()
    session.engines.clear()
    a.is_ready = False
    assert session.playable_range() is None
    assert not session.seek_master_all(5.0)
    assert a.seeks == []


def test_playable_range_prefers_ready_pair_intersection() -> None:
    session, a, b = make_session()
    b.duration = 7.0
    assert session.playable_range() == (0.0, 7.0)
    assert session.playable_range() == session.master_range()
    assert a.is_ready and b.is_ready


def test_playable_range_rejects_zero_width_pair() -> None:
    session, a, b = make_session()
    b.duration = 3.0
    session.engines["b"].state.point_a = 0.0
    session.engines["b"].state.point_b = 5.0
    assert session.master_range() == (0.0, 0.0)
    assert session.playable_range() is None
    assert not session.seek_master_all(0.0)
    assert a.seeks == []


def test_reference_fps_falls_back_to_meta() -> None:
    session, a, _b = make_session()
    session.engines.clear()
    a.fps = None
    a.meta = SimpleNamespace(fps=120.0)
    assert session.reference_fps() == 120.0


def test_reference_fps_uses_meta_with_ready_engine() -> None:
    session, a, _b = make_session()
    a.fps = None
    a.meta = SimpleNamespace(fps=120.0)
    assert session.reference_fps() == 120.0
