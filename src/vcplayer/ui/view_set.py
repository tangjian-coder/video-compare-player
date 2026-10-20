"""Ordered view registry: the single source for view ids and the active pane."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.sync import ViewId
from .video_view import VideoView


class ViewSet:
    """Owns the ordered ``ViewId -> VideoView`` mapping and active view."""

    def __init__(self, views: Iterable[VideoView] = ()) -> None:
        self._views: dict[ViewId, VideoView] = {}
        self._active: ViewId | None = None
        for view in views:
            self.add(view)

    @property
    def views(self) -> dict[ViewId, VideoView]:
        """Read-only-by-convention snapshot in insertion order."""
        return dict(self._views)

    @property
    def ids(self) -> list[ViewId]:
        """View ids in track/display order."""
        return list(self._views)

    @property
    def active(self) -> ViewId | None:
        return self._active

    @active.setter
    def active(self, view: ViewId | None) -> None:
        if view is not None and view not in self._views:
            return
        self._active = view

    def add(self, view: VideoView) -> None:
        vid = view.view_id
        if vid in self._views:
            raise ValueError(f"duplicate view id: {vid}")
        self._views[vid] = view
        if self._active is None:
            self._active = vid

    def pop(self, vid: ViewId) -> VideoView | None:
        view = self._views.pop(vid, None)
        if self._active == vid:
            self._active = next(iter(self._views), None)
        return view
