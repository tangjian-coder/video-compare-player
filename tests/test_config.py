"""Tests for config loading robustness and speed sanitization."""

from pathlib import Path

from vcplayer.config import AppConfig, _clean_speed


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def test_load_defaults_when_file_missing(tmp_path: Path) -> None:
    cfg = AppConfig.load(tmp_path / "none.json")
    assert cfg.last_dir == ""
    assert cfg.speed == 1.0
    assert cfg.window.width == 1720


def test_load_rejects_array_root(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    _write(p, "[1, 2]")
    assert AppConfig.load(p).speed == 1.0


def test_load_rejects_scalar_root(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    _write(p, "5")
    assert AppConfig.load(p).speed == 1.0


def test_load_rejects_string_root(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    _write(p, '"text"')
    assert AppConfig.load(p).last_dir == ""


def test_load_rejects_non_object_window(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    _write(p, '{"window": 3}')
    assert AppConfig.load(p).window.width == 1720


def test_load_rejects_corrupt_json(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    _write(p, "{not json")
    assert AppConfig.load(p).speed == 1.0


def test_speed_is_clamped_to_playable_range(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    _write(p, '{"speed": 99}')
    assert AppConfig.load(p).speed == 4.0
    _write(p, '{"speed": 0}')
    assert AppConfig.load(p).speed == 0.1
    _write(p, '{"speed": "abc"}')
    assert AppConfig.load(p).speed == 1.0
    _write(p, '{"speed": NaN}')
    assert AppConfig.load(p).speed == 1.0


def test_clean_speed_handles_garbage() -> None:
    assert _clean_speed(None) == 1.0
    assert _clean_speed([1]) == 1.0
    assert _clean_speed(float("inf")) == 1.0
    assert _clean_speed(0.5) == 0.5


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "cfg.json"
    cfg = AppConfig(last_dir="D:/x", speed=0.5)
    cfg.save(p)
    loaded = AppConfig.load(p)
    assert loaded.last_dir == "D:/x"
    assert loaded.speed == 0.5
