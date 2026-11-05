"""Internal pane-drag payload and video-file drop helpers.

One codec for the custom MIME avoids three slightly different decoders in
the surface, the pane and the grid host. The file-extension filter lives
here too because it is only used for drag/drop payloads.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData

VIEW_MIME = "application/x-vcplayer-view"
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".ts", ".m2ts", ".mts", ".m4v"}


def encode_view_id(view_id: str) -> bytes:
    return view_id.lower().encode("ascii")


def decode_view_id(mime: QMimeData | None) -> str | None:
    """Source view id of an internal pane drag, or None for other drags."""
    if mime is None or not mime.hasFormat(VIEW_MIME):
        return None
    return bytes(mime.data(VIEW_MIME).data()).decode("ascii", errors="ignore") or None


def make_view_mime(view_id: str) -> QMimeData:
    mime = QMimeData()
    mime.setData(VIEW_MIME, encode_view_id(view_id))
    return mime


def video_paths(mime: QMimeData | None) -> list[Path]:
    """Dropped local video files, preserving drag order."""
    if mime is None or not mime.hasUrls():
        return []
    paths: list[Path] = []
    for url in mime.urls():
        local = url.toLocalFile()
        if local and Path(local).suffix.lower() in VIDEO_EXTS:
            paths.append(Path(local))
    return paths
