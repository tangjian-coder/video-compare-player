"""JSON-backed application settings."""

from __future__ import annotations

import json
import logging
import math
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


def _resource_root() -> Path:
    """Read-only resources root (vendor/, assets/).

    Frozen: PyInstaller onefile extraction dir (sys._MEIPASS).
    Dev: the project checkout root.
    """
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent  # onedir fallback
    return Path(__file__).resolve().parents[2]


def _data_root() -> Path:
    """Writable data root (configs/, logs/).

    Frozen: %LOCALAPPDATA%\\vcplayer so the exe can live in a read-only dir.
    Dev: the project checkout root.
    """
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / "vcplayer"
        return Path.home() / ".vcplayer"
    return Path(__file__).resolve().parents[2]


RESOURCE_ROOT = _resource_root()
DATA_ROOT = _data_root()
CONFIG_PATH = DATA_ROOT / "configs" / "settings.json"
LOG_DIR = DATA_ROOT / "logs"
VENDOR_DIR = RESOURCE_ROOT / "vendor"
ASSETS_DIR = RESOURCE_ROOT / "assets"

# Playback speed bounds, mirroring the UI speed steps (0.1x .. 4.0x).
_MIN_SPEED = 0.1
_MAX_SPEED = 4.0


def _clean_speed(value: object) -> float:
    """Coerce a raw config value into a finite, playable speed."""
    try:
        speed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(speed):
        return 1.0
    return min(_MAX_SPEED, max(_MIN_SPEED, speed))


@dataclass
class WindowConfig:
    """Main window geometry."""

    width: int = 1720
    height: int = 960


@dataclass
class AppConfig:
    """Persisted application settings."""

    last_dir: str = ""
    speed: float = 1.0
    window: WindowConfig = field(default_factory=WindowConfig)

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> AppConfig:
        """Load settings; fall back to defaults on any error."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise TypeError(f"config root must be an object, got {type(raw).__name__}")
            win = raw.get("window", {})
            if not isinstance(win, dict):
                raise TypeError("config 'window' must be an object")
            return cls(
                last_dir=str(raw.get("last_dir", "")),
                speed=_clean_speed(raw.get("speed", 1.0)),
                window=WindowConfig(
                    width=int(win.get("width", 1720)),
                    height=int(win.get("height", 960)),
                ),
            )
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("failed to load config %s: %s; using defaults", path, exc)
            return cls()

    def save(self, path: Path = CONFIG_PATH) -> None:
        """Persist settings; never raise."""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("failed to save config %s: %s", path, exc)
