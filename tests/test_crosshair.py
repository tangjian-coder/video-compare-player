"""Tests for the crosshair geometry: pane-center mapping and ASS build.

The overlay renders via mpv's osd-overlay (ass-events); these lock the
math that pins the 1px red cross to the PANE center (a fixed screen
reference, NOT the video content center) and the ASS vector that draws
it, including the \\pos compensation for the embedded libass quirk.
"""

from __future__ import annotations

from vcplayer.core.player import (
    _CROSSHAIR_ARM_PX,
    _CROSSHAIR_OSD_ID,
    build_crosshair_ass,
    pane_center_from_osd,
)

# -- pane_center_from_osd -------------------------------------------------------


def test_center_none_dims_is_origin() -> None:
    assert pane_center_from_osd(None) == (0.0, 0.0)
    assert pane_center_from_osd({}) == (0.0, 0.0)


def test_center_ignores_letterbox_margins() -> None:
    # Video content may be letterboxed, but the crosshair is anchored to
    # the pane center: margins must NOT move it.
    dims = {"w": 640, "h": 480, "ml": 0, "mr": 0, "mt": 60, "mb": 60}
    assert pane_center_from_osd(dims) == (320.0, 240.0)


def test_center_ignores_pan_and_zoom() -> None:
    # Panned/zoomed video: margins are asymmetric, pane center stays put.
    dims = {"w": 640, "h": 480, "ml": 200, "mr": 40, "mt": 0, "mb": 0}
    cx, cy = pane_center_from_osd(dims)
    assert cx == 320.0
    assert cy == 240.0


def test_center_degenerate_dims_is_origin() -> None:
    assert pane_center_from_osd({"w": 0, "h": 0}) == (0.0, 0.0)
    assert pane_center_from_osd({"w": -5, "h": 100}) == (0.0, 0.0)


def test_center_simple_dims_is_half_size() -> None:
    dims = {"w": 100, "h": 50}
    assert pane_center_from_osd(dims) == (50.0, 25.0)


# -- build_crosshair_ass -----------------------------------------------------------


def test_ass_pins_position_with_compensation() -> None:
    # arm is added to \pos to offset the embedded libass quirk: \pos
    # lands on the bbox bottom-right corner, so compensating by +arm
    # re-centers the cross exactly at the requested point.
    ass = build_crosshair_ass(400.0, 123.5)
    cx = 400.0 + _CROSSHAIR_ARM_PX
    cy = 123.5 + _CROSSHAIR_ARM_PX
    assert f"\\pos({cx:.1f},{cy:.1f})" in ass
    assert "\\an5" in ass


def test_ass_is_single_pure_red_layer() -> None:
    ass = build_crosshair_ass(0, 0)
    # One dialogue line only; pure red fill; no second layer.
    assert ass.count("\n") == 0
    assert "\\1c&H0000FF&" in ass
    assert "\\1c&HFFFFFF&" not in ass
    assert "\\1c&H000000&" not in ass


def test_ass_cross_is_one_unit_wide_and_centered() -> None:
    # arm=120: both bars span the origin symmetrically, so they intersect
    # exactly at the bbox center; thickness is exactly 1 unit on each side.
    ass = build_crosshair_ass(0, 0, arm=120)
    assert "m -120 0 l 120 0 l 120 1 l -120 1" in ass
    assert "m 0 -120 l 0 120 l 1 120 l 1 -120" in ass


def test_ass_kills_outline_and_shadow() -> None:
    ass = build_crosshair_ass(0, 0)
    assert "\\3a&HFF&" in ass  # no style outline
    assert "\\4a&HFF&" in ass  # no style shadow


def test_overlay_id_is_private() -> None:
    # Distinct from small integers scripts commonly pick (0/1/2).
    assert _CROSSHAIR_OSD_ID >= 1000


# -- set_crosshair behavior with a stub mpv --------------------------------------


class _StubMpv:
    """Minimal stand-in for mpv.MPV: records commands, serves dims."""

    def __init__(self, dims: object = None, error: type[Exception] | None = None) -> None:
        self.commands: list[dict[str, object]] = []
        self.osd_dimensions = dims
        self._error = error

    def command(self, name: str, **kwargs: object) -> None:
        if self._error is not None:
            raise self._error("boom")
        kwargs["_name"] = name
        self.commands.append(kwargs)


def _stub_player(dims: object = None, error: type[Exception] | None = None):
    """PlayerController without a real libmpv core."""
    from vcplayer.core.player import PlayerController

    p = PlayerController.__new__(PlayerController)
    p._mpv = _StubMpv(dims, error)
    p._terminated = False
    p._crosshair = False
    p._last_cross_dims = None
    return p


_DIMS = {"w": 640, "h": 480, "ml": 0, "mr": 0, "mt": 60, "mb": 60}


def test_set_crosshair_on_pushes_named_overlay_at_pane_center() -> None:
    p = _stub_player(_DIMS)
    p.set_crosshair(True)
    assert p.crosshair is True
    assert len(p._mpv.commands) == 1
    cmd = p._mpv.commands[0]
    assert cmd["_name"] == "osd-overlay"
    assert cmd["id"] == _CROSSHAIR_OSD_ID
    assert cmd["format"] == "ass-events"
    assert cmd["res_x"] == 640 and cmd["res_y"] == 480
    data = str(cmd["data"])
    # Pane center (320, 240) + arm compensation; letterbox margins ignored.
    assert f"\\pos({320 + _CROSSHAIR_ARM_PX}.0,{240 + _CROSSHAIR_ARM_PX}.0)" in data


def test_set_crosshair_off_removes_with_empty_data() -> None:
    p = _stub_player(_DIMS)
    p.set_crosshair(True)
    p.set_crosshair(False)
    assert p.crosshair is False
    assert len(p._mpv.commands) == 2
    cmd = p._mpv.commands[1]
    assert cmd["format"] == "none"
    assert cmd["data"] == ""


def test_set_crosshair_never_raises_on_runtime_error() -> None:
    p = _stub_player(_DIMS, error=RuntimeError)
    p.set_crosshair(True)  # must not raise
    p.set_crosshair(False)  # must not raise


def test_push_dedups_unchanged_dims() -> None:
    p = _stub_player(_DIMS)
    p.set_crosshair(True)
    p._on_osd_dims_changed("osd-dimensions", _DIMS)  # same size
    assert len(p._mpv.commands) == 1  # no redundant push
    p._mpv.osd_dimensions = {"w": 800, "h": 600}  # resized
    p._on_osd_dims_changed("osd-dimensions", None)
    assert len(p._mpv.commands) == 2


def test_observer_does_not_push_when_disabled() -> None:
    p = _stub_player(_DIMS)
    p._on_osd_dims_changed("osd-dimensions", _DIMS)
    assert p._mpv.commands == []
