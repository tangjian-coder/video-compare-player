"""CLI entry: python -m vcplayer [a.mp4 b.mp4 ...] [--debug]."""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import LOG_DIR
from .ui.layout import MAX_VIEWS


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

    log_dir = LOG_DIR
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
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
        prog="vcplayer", description="Multi-video synchronized comparison player"
    )
    parser.add_argument(
        "videos",
        nargs="*",
        type=Path,
        help="2-4 videos: first two fill views A/B, each extra adds a view",
    )
    parser.add_argument("--debug", action="store_true", help="enable debug logging")
    args = parser.parse_args(argv)
    if len(args.videos) > MAX_VIEWS:
        parser.error(f"at most {MAX_VIEWS} videos (one per view)")

    _setup_logging(args.debug)
    logger = logging.getLogger(__name__)
    logger.info("starting vcplayer")

    from .app import run  # deferred: Qt import after logging is up

    try:
        return run(videos=args.videos)
    except Exception:
        # pythonw.exe has no console: without this the traceback is lost.
        logger.exception("fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
