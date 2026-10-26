"""Ensemble transport: play/pause, seeking, stepping and drift regulation."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QTimer

from ..config import AppConfig
from ..core.player import PlayerController
from ..core.sync import ViewId
from .session_controller import SessionController
from .video_view import VideoView

logger = logging.getLogger(__name__)


class TransportController:
    """Coordinates transport across all views.

    ``playing`` is the ensemble intent, not a mirror of mpv: it becomes true
    only after at least one ready player was started, and is reconciled
    against real player state on every UI tick.
    """

    def __init__(
        self,
        session: SessionController,
        config: AppConfig,
        notify: Callable[[str], None],
    ) -> None:
        self._session = session
        self._config = config
        self._notify = notify
        self.playing = False

    # -- state ---------------------------------------------------------------

    @property
    def master_player(self) -> PlayerController:
        return self._session.master_player

    def any_engine_ready(self) -> bool:
        return self._session.any_ready

    def reference_ready(self) -> bool:
        return self._session.reference_ready

    def playable_range(self) -> tuple[float, float] | None:
        return self._session.playable_range()

    def effective_playing(self) -> bool:
        """Playback state the global button should display.

        A sole loaded view started from its pane button already behaves as
        global playback, so the global icon must show Pause for it.
        """
        if self.playing:
            return True
        ready = [
            view
            for view in self._session.views.values()
            if view.surface.has_player and view.player.is_ready
        ]
        return (
            len(ready) == 1
            and ready[0].player.is_playing
            and not self._at_stream_end(ready[0].player)
        )

    def _require_transport(self) -> bool:
        if self.reference_ready():
            return True
        if self._session.has_ready_view:
            self._notify("The reference view is not ready")
        else:
            self._notify("Load a video first")
        return False

    def reference_fps(self) -> float:
        return self._session.reference_fps()

    def reconcile(self) -> None:
        """Stop the ensemble when its reference is gone or has reached EOF."""
        if not self.playing:
            return
        master = self.master_player
        if not master.is_ready or self._at_stream_end(master):
            self.stop()
            return
        players = [view.player for view in self._session.views.values() if view.player.is_ready]
        if not players or not any(player.is_playing for player in players):
            self.playing = False

    # -- commands ------------------------------------------------------------

    def toggle(self) -> None:
        views = list(self._session.views.values())
        if self.playing:
            for view in views:
                view.player.pause()
            self.playing = False
            logger.info("playback paused")
            QTimer.singleShot(200, self.resync_if_needed)
            return
        ready = [view for view in views if view.player.is_ready]
        # A sole ready view playing on its own is paused by the same global
        # control, even while another pane is still loading. EOF falls
        # through so one press rewinds and restarts.
        if (
            len(ready) == 1
            and ready[0].player.is_playing
            and not self._at_stream_end(ready[0].player)
        ):
            ready[0].player.pause()
            logger.info("playback paused")
            return
        if not self._require_transport():
            return
        # A solo-playing view is not an ensemble session: starting the
        # ensemble pauses it first, then starts every ready view together.
        solo_playing = [view for view in views if view.player.is_playing]
        for view in solo_playing:
            view.player.pause()
        rng = self.playable_range()
        master = self.master_player.time_pos
        if rng is not None and master is not None and master >= rng[1] - 1e-6:
            self.seek_master_all(rng[0])
        self._session.resync_ready()
        started = 0
        for view in views:
            if view.player.is_ready:
                view.player.play()
                started += 1
        self.playing = started > 0
        if started:
            logger.info("playback started, speed=%.2f", self._config.speed)
        else:
            self._notify("Videos are still loading")

    def stop(self) -> None:
        """Pause every view; positions stay put."""
        self.playing = False
        views = self._session.views
        if any(view.player.is_playing for view in views.values()):
            for view in views.values():
                view.player.pause()
            logger.debug("playback stopped")

    def resync_if_needed(self) -> None:
        """Snap followers only after a pause, never mid-playback."""
        if self.playing or not self.any_engine_ready():
            return
        if any(
            view.player.is_ready and view.player.is_playing for view in self._session.views.values()
        ):
            return
        self._session.resync_ready()

    def seek_master_all(self, master: float, *, exact: bool = True) -> bool:
        if not self._session.seek_master_all(master, exact=exact):
            if self.any_engine_ready():
                self._notify("No common range between loaded views")
            return False
        return True

    def step(self, frames: int) -> None:
        if not self._require_transport():
            return
        self.stop()
        master = self.master_player.time_pos
        if master is None:
            return
        master += frames / self.reference_fps()
        rng = self.playable_range()
        if rng is not None:
            master = min(max(master, rng[0]), rng[1])
        self.seek_master_all(master)

    def step_view(self, view: ViewId, frames: int) -> None:
        pane = self._session.views.get(view)
        if pane is None:
            return
        self.stop()
        pane.player.step_frames(frames)

    def toggle_view(self, view: ViewId) -> None:
        pane = self._session.views.get(view)
        if pane is None:
            return
        player = pane.player
        if not player.is_ready:
            self._notify(f"{view.upper()}: load a video first")
            return
        was_playing = player.is_playing
        if self.playing:
            self.stop()
        if was_playing:
            player.pause()
        else:
            player.play()
        if player.is_playing and self._at_stream_end(player):
            player.seek_exact(0.0)
            player.play()

    def goto_first(self) -> None:
        if not self._require_transport():
            return
        self.stop()
        rng = self.playable_range()
        if rng is None:
            if self.any_engine_ready():
                self._notify("No common range between loaded views")
            return
        self.seek_master_all(rng[0])

    def goto_last(self) -> None:
        if not self._require_transport():
            return
        self.stop()
        rng = self.playable_range()
        if rng is None:
            if self.any_engine_ready():
                self._notify("No common range between loaded views")
            return
        self.seek_master_all(max(rng[0], rng[1] - 1.0 / self.reference_fps()))

    def scrub_preview(self, t: float) -> None:
        if not self._require_transport():
            return
        self.stop()
        self.seek_master_all(t, exact=False)

    def seek_committed(self, t: float) -> None:
        if not self._require_transport():
            return
        self.stop()
        self.seek_master_all(t)

    def set_speed(self, speed: float) -> None:
        self._config.speed = speed
        for view in self._session.views.values():
            view.player.set_speed(speed)

    def apply_speed_to(self, view: VideoView) -> None:
        view.player.set_speed(self._config.speed)

    def reset_speeds(self) -> None:
        for view in self._session.views.values():
            if view.surface.has_player:
                view.player.set_speed(self._config.speed)

    def reset_speed(self, view: ViewId) -> None:
        pane = self._session.views.get(view)
        if pane is not None and pane.surface.has_player:
            pane.player.set_speed(self._config.speed)

    def regulate(self) -> None:
        master = self.master_player
        if self.playing and master.is_ready and not self._at_stream_end(master):
            self._session.regulate(self._config.speed)

    @staticmethod
    def _at_stream_end(player: PlayerController) -> bool:
        t, dur = player.time_pos, player.duration
        return t is not None and dur is not None and t >= dur - 1e-6
