"""Tests for source orientation detection and absolute rotation."""

from __future__ import annotations

from vcplayer.core.models import display_orientation
from vcplayer.core.player import PlayerController


class _StubMpv:
    def __init__(self, params: object = None) -> None:
        self.video_params = params
        self.video_rotate = 0
        self.video_pan_x = 0.2
        self.video_pan_y = 0.3


def _player(params: object = None) -> PlayerController:
    player = PlayerController.__new__(PlayerController)
    player._mpv = _StubMpv(params)
    player._terminated = False
    player._rotation = 0
    return player


def test_display_orientation_uses_rotation_metadata() -> None:
    assert display_orientation(1080, 1920, 0) == "portrait"
    assert display_orientation(1920, 1080, 0) == "landscape"
    assert display_orientation(1920, 1080, 90) == "portrait"
    assert display_orientation(1080, 1920, 90) == "landscape"
    assert display_orientation(1920, 1080, 270) == "portrait"
    assert display_orientation(100, 100, 0) == "landscape"


def test_display_orientation_rejects_invalid_dimensions() -> None:
    assert display_orientation(0, 1920) is None
    assert display_orientation(1080, 0) is None
    assert display_orientation(float("nan"), 1920) is None
    assert display_orientation(1080, float("inf")) is None


def test_player_source_orientation_uses_display_dimensions() -> None:
    portrait = _player({"dw": 1080, "dh": 1920, "rotate": 0})
    assert portrait.source_orientation == "portrait"
    metadata_rotated = _player({"w": 1920, "h": 1080, "rotate": 270})
    assert metadata_rotated.source_orientation == "portrait"
    manually_rotated = _player({"dw": 360, "dh": 640, "rotate": 90})
    manually_rotated._rotation = 90
    assert manually_rotated.source_orientation == "portrait"
    assert _player(None).source_orientation is None
    assert _player({"dw": 0, "dh": 0}).source_orientation is None


def test_player_display_dimensions_include_total_rotation() -> None:
    assert _player({"dw": 1920, "dh": 1080, "rotate": 0}).display_dimensions == (
        1920.0,
        1080.0,
    )
    assert _player({"dw": 1920, "dh": 1080, "rotate": 90}).display_dimensions == (
        1080.0,
        1920.0,
    )
    assert _player({"dw": 1920, "dh": 1080, "rotate": 180}).display_dimensions == (
        1920.0,
        1080.0,
    )
    assert _player({"dw": 1920, "dh": 1080, "rotate": 270}).display_dimensions == (
        1080.0,
        1920.0,
    )
    assert _player({"dw": 0, "dh": 1080}).display_dimensions is None


def test_set_rotation_is_absolute_and_resets_pan_once() -> None:
    player = _player()
    player.set_rotation(90)
    assert player.rotation == 90
    assert player._mpv.video_rotate == 90
    assert player._mpv.video_pan_x == 0.0
    assert player._mpv.video_pan_y == 0.0

    player._mpv.video_pan_x = 0.5
    player.set_rotation(90)
    assert player._mpv.video_pan_x == 0.5
    player.set_rotation(450)
    assert player.rotation == 90
