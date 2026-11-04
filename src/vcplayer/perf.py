"""Optional lightweight performance instrumentation."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager

logger = logging.getLogger(__name__)

_STARTED = time.perf_counter()
_ENABLED = False


def configure(enabled: bool | None = None) -> None:
    """Enable or disable performance logging (env: VCPLAYER_PERF)."""
    global _ENABLED
    if enabled is None:
        enabled = os.environ.get("VCPLAYER_PERF", "").lower() in {"1", "true", "yes"}
    _ENABLED = enabled


def enabled() -> bool:
    return _ENABLED


def begin() -> float:
    return time.perf_counter()


def process_start() -> float:
    """Timestamp from the earliest perf-module import in this process."""
    return _STARTED


def mark(name: str, start: float | None = None, **fields: object) -> None:
    """Log a milestone, optionally with elapsed milliseconds since `start`."""
    if not _ENABLED:
        return
    parts = [name]
    if start is not None:
        parts.append(f"{((time.perf_counter() - start) * 1000.0):.1f}ms")
    parts.extend(f"{key}={value}" for key, value in fields.items())
    logger.info("PERF %s", " ".join(parts))


@contextmanager
def span(name: str, **fields: object) -> Iterator[None]:
    """Time a block and log it when performance logging is enabled."""
    if not _ENABLED:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        mark(name, started, **fields)
