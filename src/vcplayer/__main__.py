"""CLI entry: python -m vcplayer [left.mp4] [right.mp4] [--debug]."""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import PROJECT_ROOT


def _setup_logging(debug: bool) -> None:
    """Console + rotating file logging."""
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    if sys.stderr is not None:  # pythonw.exe has no console streams
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        root.addHandler(console)

    log_dir = PROJECT_ROOT / "logs"
    try:
        log_dir.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "vcplayer.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
    except OSError:
        # No file log (read-only dir / locked file): degrade to console only
        # rather than dying before the app can start.
        return
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and start the application."""
    parser = argparse.ArgumentParser(
        prog="vcplayer", description="Dual-video synchronized comparison player"
    )
    parser.add_argument("left", nargs="?", type=Path, help="video for the left view")
    parser.add_argument("right", nargs="?", type=Path, help="video for the right view")
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)

    _setup_logging(args.debug)
    logger = logging.getLogger(__name__)
    logger.info("starting vcplayer")

    from .app import run  # deferred: Qt import after logging is up

    try:
        return run(left=args.left, right=args.right)
    except Exception:
        # pythonw.exe has no console: without this the traceback is lost.
        logger.exception("fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
