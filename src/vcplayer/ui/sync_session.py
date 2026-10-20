"""Sync session: owns follower engines and the explicit master view."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..core.player import PlayerController
from ..core.sync import (
    SyncEngine,
    SyncError,
    ViewId,
    intersect_master_ranges,
)
from .video_view import VideoView


class SyncSession:
    """Owns engines keyed by follower; the master view is explicit."""

    def __init__(self, views: Callable[[], Mapping[ViewId, VideoView]]) -> None:
        self._views = views
        self.master: ViewId = "a"
        self.engines: dict[ViewId, SyncEngine] = {}

    def add_follower(self, view: ViewId) -> bool:
        """Create the engine for one newly added follower view."""
        views = self._views()
        master = views.get(self.master)
        pane = views.get(view)
        if master is None or pane is None:
            return False
        if not master.surface.has_player or not pane.surface.has_player:
            return False
        self.engines[view] = SyncEngine(master.player, pane.player)
        return True

    def rebuild(self) -> None:
        """Recreate every follower engine against the current master."""
        self.engines.clear()
        views = self._views()
        master = views.get(self.master)
        if master is None or not master.surface.has_player:
            return
        for vid, pane in views.items():
            if vid == self.master or not pane.surface.has_player:
                continue
            self.engines[vid] = SyncEngine(master.player, pane.player)

    def set_master(self, master: ViewId) -> bool:
        """Switch the reference view and rebuild all follower engines.

        Idempotent and validated: an invalid or unchanged master does not
        discard the current anchors.
        """
        views = self._views()
        pane = views.get(master)
        if pane is None or not pane.surface.has_player:
            return False
        if master == self.master:
            return False
        self.master = master
        self.rebuild()
        return True

    def remove_view(self, view: ViewId, *, replacement: ViewId | None = None) -> bool:
        """Drop a removed view's engine, promoting a replacement if needed."""
        was_master = view == self.master
        self.engines.pop(view, None)
        if not was_master:
            return False
        views = self._views()
        candidate = replacement if replacement in views else None
        if candidate is None:
            candidate = next((vid for vid in views if vid != view), None)
        self.master = candidate if candidate is not None else "a"
        self.rebuild()
        return True

    @property
    def master_player(self) -> PlayerController:
        return self._views()[self.master].player

    @property
    def any_ready(self) -> bool:
        return any(eng.ready for eng in self.engines.values())

    def master_range(self) -> tuple[float, float] | None:
        """Master interval where every ready pair has frames."""
        return intersect_master_ranges(self.engines.values())

    def reference_fps(self) -> float:
        for eng in self.engines.values():
            if eng.ready:
                return eng.reference_fps
        fps = self.master_player.fps
        return fps if fps and fps > 0 else 25.0

    def seek_master_all(self, master: float, *, exact: bool = True) -> bool:
        """Seek the master once and every ready follower to one position."""
        rng = self.master_range()
        if rng is None:
            return False
        master = min(max(master, rng[0]), rng[1])
        player = self.master_player
        if exact:
            player.seek_exact(master)
        else:
            player.seek_fast(master)
        for eng in self.engines.values():
            if eng.ready:
                eng.seek_follower(master, exact=exact)
                # A hard seek invalidates the EWMA the loop was built on.
                eng.reset_runtime()
        return True

    def current_anchors(self) -> dict[ViewId, float | None]:
        anchors: dict[ViewId, float | None] = {self.master: None}
        anchors.update({vid: None for vid in self.engines})
        for vid, eng in self.engines.items():
            if eng.state.is_complete:
                if anchors[self.master] is None:
                    anchors[self.master] = eng.state.point_a
                anchors[vid] = eng.state.point_b
        return anchors

    def current_offsets(self) -> dict[ViewId, float]:
        return {vid: eng.offset for vid, eng in self.engines.items() if eng.state.is_complete}

    def align_current(self) -> tuple[dict[ViewId, float], str | None]:
        """Sync every ready follower to the current master frame, all pairs."""
        offsets: dict[ViewId, float] = {}
        error: str | None = None
        for vid, eng in self.engines.items():
            if not eng.ready:
                continue
            try:
                offsets[vid] = eng.sync_here()
            except SyncError as exc:
                error = str(exc)
        return offsets, error

    def affected_by(self, view: ViewId) -> list[ViewId]:
        """Follower engines whose anchors involve a reloaded view."""
        return list(self.engines) if view == self.master else [view]

    def invalidate(self, view: ViewId) -> list[ViewId]:
        """Clear anchors involving a reloaded view; returns cleared followers."""
        affected = self.affected_by(view)
        cleared: list[ViewId] = []
        for vid in affected:
            eng = self.engines.get(vid)
            if eng is not None and eng.state.is_complete:
                eng.clear_sync()
                cleared.append(vid)
        return cleared

    def clear_sync(self) -> None:
        for eng in self.engines.values():
            eng.clear_sync()

    def regulate(self, speed: float) -> None:
        for eng in self.engines.values():
            if eng.ready and eng.state.is_complete:
                eng.regulate(speed)
