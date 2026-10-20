"""Tests for the per-player load state machine."""

from __future__ import annotations

from pathlib import Path

import pytest

from vcplayer.core.models import VideoMeta
from vcplayer.core.player import LoadState, PlayerController, PlayerError


class StubMpv:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.commands: list[str] = []
        self.duration: float | None = 10.0
        self.video_zoom = 0.5
        self.video_pan_x = 0.1
        self.video_pan_y = 0.1
        self.video_rotate = 90

    def command(self, name: str, *args: object, **kwargs: object) -> None:
        self.commands.append(name)
        if self.fail:
            raise SystemError("boom")
        if name == "stop":
            self.duration = None


def _player(mpv: StubMpv, state: LoadState) -> PlayerController:
    player = PlayerController.__new__(PlayerController)
    player._mpv = mpv
    player._terminated = False
    player._meta = None
    player._load_state = state
    player._load_error = None
    player._expected_path = None
    player._rotation = 0
    player._crosshair = False
    player._last_cross_dims = None
    return player


def test_failed_load_stops_media_and_resets_view() -> None:
    mpv = StubMpv()
    player = _player(mpv, LoadState.LOADING)
    player._meta = VideoMeta(Path("old.mp4"), 10.0, 60.0, 1920, 1080)
    player._expected_path = Path("new.mp4").resolve()

    player.mark_load_failed("bad")

    assert player.load_state is LoadState.FAILED
    assert player.load_error == "bad"
    assert player.meta is None
    assert player.is_ready is False
    assert mpv.commands == ["stop"]
    assert mpv.duration is None
    assert mpv.video_zoom == 0.0


def test_synchronous_load_failure_restores_previous_state(tmp_path: Path) -> None:
    clip = tmp_path / "new.mp4"
    clip.write_bytes(b"x")
    mpv = StubMpv(fail=True)
    player = _player(mpv, LoadState.READY)
    old_meta = VideoMeta(Path("old.mp4"), 10.0, 60.0, 1920, 1080)
    player._meta = old_meta
    player._expected_path = Path("old.mp4").resolve()

    with pytest.raises(PlayerError):
        player.load(clip)

    assert player.meta is old_meta
    assert player.load_state is LoadState.READY
    assert player._expected_path == Path("old.mp4").resolve()
