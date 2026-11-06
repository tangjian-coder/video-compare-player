"""Ensemble transport: play/pause, seeking, stepping and drift regulation."""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from queue import Empty, SimpleQueue
from threading import Lock
from typing import Any

from PySide6.QtCore import QTimer

from ..config import AppConfig
from ..core.player import PlayerController
from ..core.sync import ViewId
from .session_controller import SessionController
from .sync_session import AsyncSeek
from .video_view import VideoView

logger = logging.getLogger(__name__)

PREVIEW_TIMEOUT_S = 2.0
EXACT_SEEK_TIMEOUT_S = 5.0


@dataclass
class _SeekBatch:
    kind: str
    token: int
    remaining: int
    error: str | None = None


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
        self._seek_seq = 0
        self._seek_lock = Lock()
        self._seek_completions: SimpleQueue[tuple[str, int, str | None]] = SimpleQueue()
        self._dispatch_error: str | None = None
        self._preview_inflight = False
        self._preview_token = 0
        self._preview_pending: float | None = None
        self._preview_target: float | None = None
        self._preview_started = 0.0
        self._preview_futures: list[Future[Any]] = []
        self._commit_inflight = False
        self._commit_settling = False
        self._commit_token = 0
        self._commit_target: float | None = None
        self._commit_targets: dict[ViewId, float] = {}
        self._commit_from: dict[ViewId, float | None] = {}
        self._commit_started = 0.0
        self._commit_futures: list[Future[Any]] = []
        self._play_after_seek = False

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

    def any_player_playing(self) -> bool:
        """True when any ready view is actually playing, including solo mode."""
        return any(
            view.surface.has_player
            and view.player.is_ready
            and view.player.is_playing
            and not self._at_stream_end(view.player)
            for view in self._session.views.values()
        )

    def _require_transport(self) -> bool:
        if self._session.has_missing_source:
            self._notify("Reload or clear the missing source first")
            return False
        if self.reference_ready():
            return True
        if self._session.has_ready_view:
            self._notify("The reference view is not ready")
        else:
            self._notify("Load a video first")
        return False

    @property
    def exact_seek_pending(self) -> bool:
        """True until the latest exact seek has completed and landed."""
        return self._commit_inflight or self._commit_settling

    @property
    def preview_pending(self) -> bool:
        """True while a scrub preview is still being applied."""
        return self._preview_inflight or self._preview_pending is not None

    def poll_seek_completions(self) -> bool:
        """Drain async seek completions; return True when exact seek settled."""
        exact_done = False
        while True:
            try:
                kind, token, error = self._seek_completions.get_nowait()
            except Empty:
                break
            if kind == "preview":
                if token != self._preview_token:
                    continue
                self._preview_inflight = False
                self._preview_futures.clear()
                if self._preview_pending is None:
                    self._preview_target = None
                elif not self._preview_inflight:
                    if self.playing or self.any_player_playing() or self._play_after_seek:
                        self._preview_pending = None
                        self._preview_target = None
                    else:
                        target = self._preview_pending
                        self._preview_pending = None
                        self._start_preview(target)
                continue
            if token != self._commit_token:
                continue
            self._commit_futures.clear()
            if error:
                self._cancel_commit_futures()
                self._notify(f"Seek failed: {error}")
                self._finish_commit(success=False)
                exact_done = True
            else:
                self._commit_inflight = False
                self._commit_settling = True
                self._commit_started = time.monotonic()

        now = time.monotonic()
        if self._preview_inflight and now - self._preview_started > PREVIEW_TIMEOUT_S:
            logger.warning("preview seek timed out; dropping in-flight preview")
            self._cancel_preview_futures()
            self._preview_inflight = False
            if self._preview_pending is not None:
                target = self._preview_pending
                self._preview_pending = None
                self._start_preview(target)
            else:
                self._preview_target = None

        if self.exact_seek_pending and now - self._commit_started > EXACT_SEEK_TIMEOUT_S:
            logger.warning("exact seek timed out; releasing transport")
            self._cancel_commit_futures()
            self._notify("Seek timed out; try again")
            self._finish_commit(success=False)
            exact_done = True
        elif self._commit_settling and self._target_landed():
            self._finish_commit(success=True)
            exact_done = True
        return exact_done

    def _dispatch_async(
        self, master: float, *, exact: bool, kind: str
    ) -> tuple[int, list[AsyncSeek]] | None:
        self._dispatch_error = None
        try:
            seeks = self._session.seek_master_all_async(master, exact=exact)
        except Exception as exc:  # a broken mpv core must not escape a Qt slot
            self._dispatch_error = str(exc)
            logger.warning("seek dispatch failed: %s", exc, exc_info=True)
            self._notify(f"Seek failed: {exc}")
            return None
        if not seeks:
            return None
        self._seek_seq += 1
        token = self._seek_seq
        batch = _SeekBatch(kind=kind, token=token, remaining=len(seeks))

        def completed(future: Future[Any], batch: _SeekBatch = batch) -> None:
            error: str | None = None
            try:
                future.result()
            except Exception as exc:  # mpv command failed
                error = str(exc)
            with self._seek_lock:
                batch.remaining -= 1
                if error and batch.error is None:
                    batch.error = error
                finished = batch.remaining == 0
                final_error = batch.error
            if finished:
                self._seek_completions.put((batch.kind, batch.token, final_error))

        for seek in seeks:
            seek.future.add_done_callback(completed)
        return token, seeks

    def _start_preview(self, target: float) -> bool:
        dispatched = self._dispatch_async(target, exact=False, kind="preview")
        if dispatched is None:
            self._preview_inflight = False
            self._preview_target = None
            if self._dispatch_error is None and self.any_engine_ready():
                self._notify("No common range between loaded views")
            return False
        token, seeks = dispatched
        self._preview_token = token
        self._preview_futures = [seek.future for seek in seeks]
        self._preview_inflight = True
        self._preview_started = time.monotonic()
        return True

    def _commit_seek(self, target: float) -> bool:
        rng = self.playable_range()
        if rng is not None:
            target = min(max(target, rng[0]), rng[1])
        self._cancel_preview_state()
        self._cancel_commit_futures()
        self._commit_inflight = False
        self._commit_settling = False
        self._commit_target = None
        self._commit_targets = {}
        self._commit_from = {}
        self._play_after_seek = False
        self._commit_token = -1

        from_times = {vid: pane.player.time_pos for vid, pane in self._session.views.items()}
        dispatched = self._dispatch_async(target, exact=True, kind="exact")
        if dispatched is None:
            if self._dispatch_error is None and self.any_engine_ready():
                self._notify("No common range between loaded views")
            return False
        token, seeks = dispatched
        self._commit_token = token
        self._commit_targets = {seek.view: seek.target for seek in seeks}
        self._commit_from = {seek.view: from_times.get(seek.view) for seek in seeks}
        self._commit_target = self._commit_targets.get(self._session.master, target)
        self._commit_futures = [seek.future for seek in seeks]
        self._commit_inflight = True
        self._commit_started = time.monotonic()
        return True

    def _target_landed(self) -> bool:
        if not self._commit_targets:
            return False
        reference_fps = self.reference_fps()
        for view_id, target in self._commit_targets.items():
            pane = self._session.views.get(view_id)
            if pane is None or not pane.player.is_ready:
                return False
            current = pane.player.time_pos
            if current is None:
                return False
            fps = pane.player.fps or reference_fps
            tolerance = max(1e-3, 0.9 / max(fps, 1.0))
            if abs(current - target) > tolerance:
                return False
            started = self._commit_from.get(view_id)
            if (
                started is not None
                and abs(target - started) > tolerance
                and abs(current - started) <= 1e-6
            ):
                return False
        return True

    def _finish_commit(self, *, success: bool) -> None:
        self._commit_inflight = False
        self._commit_settling = False
        self._commit_target = None
        self._commit_targets = {}
        self._commit_from = {}
        self._commit_futures.clear()
        self._commit_token = -1
        if self._play_after_seek:
            self._play_after_seek = False
            if success:
                self._resync_and_play()

    def _cancel_preview_futures(self) -> None:
        for future in self._preview_futures:
            with contextlib.suppress(Exception):
                future.cancel()
        self._preview_futures.clear()

    def _cancel_preview_state(self) -> None:
        self._cancel_preview_futures()
        self._preview_inflight = False
        self._preview_pending = None
        self._preview_target = None
        self._preview_token = -1

    def _cancel_commit_futures(self) -> None:
        for future in self._commit_futures:
            with contextlib.suppress(Exception):
                future.cancel()
        self._commit_futures.clear()

    def cancel_pending_seeks(self) -> None:
        """Drop in-flight seek state when the session changes underneath it."""
        self._cancel_preview_state()
        self._cancel_commit_futures()
        self._commit_inflight = False
        self._commit_settling = False
        self._commit_target = None
        self._commit_targets = {}
        self._commit_from = {}
        self._commit_token = -1
        self._play_after_seek = False

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
        self._cancel_preview_state()
        if self.exact_seek_pending:
            # A second press cancels the queued play intent.
            self._play_after_seek = not self._play_after_seek
            logger.info("play-after-seek %s", "armed" if self._play_after_seek else "cancelled")
            return
        rng = self.playable_range()
        master = self.master_player.time_pos
        at_end = rng is not None and master is not None and master >= rng[1] - 1e-6
        if at_end:
            assert rng is not None
            if self._commit_seek(rng[0]):
                self._play_after_seek = True
                return
        self._resync_and_play()

    def _resync_and_play(self) -> None:
        self._play_after_seek = False
        self._cancel_preview_state()
        if self.exact_seek_pending:
            self._play_after_seek = True
            return
        self._session.resync_ready(async_seek=False)
        started = 0
        for view in self._session.views.values():
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
        self._play_after_seek = False
        views = self._session.views
        if any(view.player.is_playing for view in views.values()):
            for view in views.values():
                view.player.pause()
            logger.debug("playback stopped")

    def resync_if_needed(self) -> None:
        """Snap followers only after a pause, never mid-playback."""
        if (
            self.playing
            or self.exact_seek_pending
            or self.preview_pending
            or self._play_after_seek
            or not self.any_engine_ready()
        ):
            return
        if any(
            view.player.is_ready and view.player.is_playing for view in self._session.views.values()
        ):
            return
        # Delayed pause resync must stay inside the transport state machine;
        # an untracked async future can land after the user resumes playback.
        self._session.resync_ready(async_seek=False)

    def seek_master_all(self, master: float, *, exact: bool = True) -> bool:
        if self._session.has_missing_source:
            self._notify("Reload or clear the missing source first")
            return False
        if not self._session.seek_master_all(master, exact=exact):
            if self.any_engine_ready():
                self._notify("No common range between loaded views")
            return False
        return True

    def step(self, frames: int) -> None:
        if not self._require_transport():
            return
        self.stop()
        master: float | None
        if self.exact_seek_pending and self._commit_target is not None:
            master = self._commit_target
        elif self.preview_pending and self._preview_target is not None:
            master = self._preview_target
        else:
            master = self.master_player.time_pos
        if master is None:
            return
        master += frames / self.reference_fps()
        rng = self.playable_range()
        if rng is not None:
            master = min(max(master, rng[0]), rng[1])
        self._commit_seek(master)

    def step_view(self, view: ViewId, frames: int) -> bool:
        pane = self._session.views.get(view)
        if pane is None:
            return False
        if self._session.has_missing_source:
            self._notify("Reload or clear the missing source first")
            return False
        if self.exact_seek_pending or self.preview_pending:
            self._notify("Wait for the current seek to finish")
            return False
        self.stop()
        pane.player.step_frames(frames)
        return True

    def toggle_view(self, view: ViewId) -> None:
        pane = self._session.views.get(view)
        if pane is None:
            return
        if self._session.has_missing_source:
            self._notify("Reload or clear the missing source first")
            return
        player = pane.player
        if not player.is_ready:
            self._notify(f"{view.upper()}: load a video first")
            return
        if self.exact_seek_pending:
            self._notify("Wait for the current seek to finish")
            return
        was_playing = player.is_playing
        if self.playing:
            self.stop()
        if was_playing:
            player.pause()
        else:
            self._cancel_preview_state()
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
        self._commit_seek(rng[0])

    def goto_last(self) -> None:
        if not self._require_transport():
            return
        self.stop()
        rng = self.playable_range()
        if rng is None:
            if self.any_engine_ready():
                self._notify("No common range between loaded views")
            return
        self._commit_seek(max(rng[0], rng[1] - 1.0 / self.reference_fps()))

    def scrub_preview(self, t: float) -> bool:
        if not self._require_transport():
            return False
        self.poll_seek_completions()
        self.stop()
        self._cancel_commit_futures()
        self._commit_inflight = False
        self._commit_settling = False
        self._commit_target = None
        self._commit_targets = {}
        self._commit_from = {}
        self._commit_token = -1
        self._play_after_seek = False
        rng = self.playable_range()
        if rng is not None:
            t = min(max(t, rng[0]), rng[1])
        self._preview_target = t
        self._preview_pending = t
        if self._preview_inflight:
            return True
        target = self._preview_pending
        self._preview_pending = None
        return self._start_preview(target)

    def seek_committed(self, t: float) -> bool:
        if not self._require_transport():
            return False
        self.stop()
        self._preview_pending = None
        return self._commit_seek(t)

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
