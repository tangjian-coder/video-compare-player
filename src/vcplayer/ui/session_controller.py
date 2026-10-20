"""View registry, reference policy, and sync-session intents."""

from __future__ import annotations

from collections.abc import Iterable

from ..core.player import PlayerController
from ..core.sync import SyncEngine, ViewId
from .sync_session import SyncSession
from .video_view import VideoView
from .view_set import ViewSet


class SessionController:
    """Owns views, their engines, and every reference/sync transition."""

    def __init__(self, views: Iterable[VideoView] = ()) -> None:
        self._view_set = ViewSet(views)
        self._sync = SyncSession(lambda: self.views)

    @property
    def views(self) -> dict[ViewId, VideoView]:
        return self._view_set.views

    @property
    def view_ids(self) -> list[ViewId]:
        return self._view_set.ids

    @property
    def active(self) -> ViewId | None:
        return self._view_set.active

    @active.setter
    def active(self, view: ViewId | None) -> None:
        self._view_set.active = view

    @property
    def engines(self) -> dict[ViewId, SyncEngine]:
        return self._sync.engines

    @property
    def master(self) -> ViewId:
        return self._sync.master

    @property
    def master_player(self) -> PlayerController:
        return self.views[self.master].player

    @property
    def any_ready(self) -> bool:
        return self._sync.any_ready

    def add_view(self, view: VideoView) -> None:
        self._view_set.add(view)

    def add_follower(self, view: ViewId) -> bool:
        return self._sync.add_follower(view)

    def rebuild(self) -> None:
        self._sync.rebuild()

    def pop_view(self, view: ViewId, *, replacement: ViewId | None = None) -> VideoView | None:
        """Remove a view and keep the sync master valid."""
        removed = self._view_set.pop(view)
        if removed is not None:
            self._sync.remove_view(view, replacement=replacement)
        return removed

    # -- readiness -----------------------------------------------------------

    def is_ready(self, view: ViewId) -> bool:
        pane = self.views.get(view)
        return bool(
            pane is not None
            and pane.shows_surface
            and pane.surface.has_player
            and pane.player.is_ready
        )

    def is_available(self, view: ViewId) -> bool:
        pane = self.views.get(view)
        if pane is None or not pane.surface.has_player:
            return False
        return pane.shows_surface or pane.player.is_loading

    def is_waiting(self, view: ViewId) -> bool:
        pane = self.views.get(view)
        return bool(pane is not None and pane.surface.has_player and pane.player.is_loading)

    def choose_reference(self, exclude: ViewId | None = None) -> ViewId | None:
        """First fully ready view, then the first loading/visible one."""
        candidates = [vid for vid in self.view_ids if vid != exclude]
        for vid in candidates:
            if self.is_ready(vid):
                return vid
        for vid in candidates:
            if self.is_available(vid):
                return vid
        return None

    def ensure_reference(self) -> ViewId | None:
        """Promote a replacement only when the current reference is unusable."""
        current = self.views.get(self.master)
        if current is not None and self.is_ready(self.master):
            return None
        if self.is_waiting(self.master):
            return None
        candidate = self.choose_reference(exclude=self.master)
        if candidate is None:
            return None
        return candidate if self.set_master(candidate) else None

    # -- sync intents --------------------------------------------------------

    def set_master(self, view: ViewId) -> bool:
        return self._sync.set_master(view)

    def current_anchors(self) -> dict[ViewId, float | None]:
        return self._sync.current_anchors()

    def current_offsets(self) -> dict[ViewId, float]:
        return self._sync.current_offsets()

    def master_range(self) -> tuple[float, float] | None:
        return self._sync.master_range()

    def reference_fps(self) -> float:
        return self._sync.reference_fps()

    def seek_master_all(self, master: float, *, exact: bool = True) -> bool:
        return self._sync.seek_master_all(master, exact=exact)

    def align_current(self) -> tuple[dict[ViewId, float], str | None]:
        return self._sync.align_current()

    def resync_ready(self) -> None:
        for eng in self.engines.values():
            if eng.ready and eng.state.is_complete:
                eng.resync_if_needed()

    def view_time(self, view: ViewId, master: float, master_view: ViewId) -> float:
        eng = self.engines.get(view)
        return eng.view_time(view, master, master_view) if eng is not None else master

    def drifts(self) -> dict[ViewId, float | None]:
        return {vid: eng.drift() for vid, eng in self.engines.items() if eng.ready}

    def regulate(self, speed: float) -> None:
        self._sync.regulate(speed)

    def invalidate(self, view: ViewId) -> list[ViewId]:
        return self._sync.invalidate(view)

    def affected_by(self, view: ViewId) -> list[ViewId]:
        return self._sync.affected_by(view)

    def clear_sync(self) -> None:
        self._sync.clear_sync()
