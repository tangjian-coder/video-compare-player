"""Tests for SyncEngine using a fake player stub (no Qt, no libmpv)."""

from dataclasses import dataclass, field

import pytest

from vcplayer.core.sync import SyncEngine, SyncError


@dataclass
class FakePlayer:
    """Minimal PlayerLike stub recording seeks and speed changes."""

    time_pos: float | None = 0.0
    duration: float | None = 10.0
    fps: float | None = 100.0
    paused: bool = True
    speed: float = 1.0
    seeks: list[float] = field(default_factory=list)
    fast_seeks: list[float] = field(default_factory=list)
    speeds: list[float] = field(default_factory=list)

    def seek_exact(self, t: float) -> None:
        self.seeks.append(t)
        self.time_pos = t

    def seek_fast(self, t: float) -> None:
        self.fast_seeks.append(t)
        self.time_pos = t

    def pause(self) -> None:
        self.paused = True

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        self.speeds.append(speed)


def make_engine(
    offset_a: float = 0.0, offset_b: float = 0.0
) -> tuple[SyncEngine, FakePlayer, FakePlayer]:
    a, b = FakePlayer(), FakePlayer()
    eng = SyncEngine(a, b)
    if offset_a or offset_b:
        a.time_pos, b.time_pos = offset_a, offset_b
        eng.sync_here()
    return eng, a, b


def test_sync_here_captures_both_current_frames() -> None:
    a, b = FakePlayer(), FakePlayer()
    eng = SyncEngine(a, b)
    a.time_pos, b.time_pos = 2.0, 5.5
    offset = eng.sync_here()
    assert offset == pytest.approx(3.5)
    assert eng.state.point_a == pytest.approx(2.0)
    assert eng.state.point_b == pytest.approx(5.5)


def test_sync_here_requires_both_loaded() -> None:
    a, b = FakePlayer(), FakePlayer()
    eng = SyncEngine(a, b)
    a.time_pos = None
    with pytest.raises(SyncError):
        eng.sync_here()


def test_sync_here_requires_both_loaded_b_side() -> None:
    a, b = FakePlayer(), FakePlayer()
    eng = SyncEngine(a, b)
    b.time_pos = None
    with pytest.raises(SyncError):
        eng.sync_here()


def test_clear_sync_resets_offset() -> None:
    eng, _, _ = make_engine(offset_a=2.0, offset_b=5.0)
    eng.clear_sync()
    assert eng.offset == 0.0


def test_master_range_positive_offset() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)
    a.duration, b.duration = 10.0, 12.0
    assert eng.master_range() == (0.0, 9.0)  # min(10, 12 - 3)


def test_master_range_negative_offset() -> None:
    eng, a, b = make_engine(offset_a=5.0, offset_b=3.0)
    a.duration, b.duration = 10.0, 12.0
    assert eng.master_range() == (2.0, 10.0)  # lo = -offset


def test_seek_master_clamps_and_maps() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)
    a.duration = b.duration = 10.0
    m = eng.seek_master(99.0)
    assert m == pytest.approx(7.0)  # hi = min(10, 10 - 3)
    assert a.seeks[-1] == pytest.approx(7.0)
    assert b.seeks[-1] == pytest.approx(10.0)  # m + offset


def test_seek_master_clamps_to_lower_bound_negative_offset() -> None:
    eng, a, b = make_engine(offset_a=5.0, offset_b=3.0)  # offset -2
    a.duration = b.duration = 10.0
    m = eng.seek_master(0.0)  # below lo = 2
    assert m == pytest.approx(2.0)
    assert a.seeks[-1] == pytest.approx(2.0)
    assert b.seeks[-1] == pytest.approx(0.0)  # m + offset


def test_master_range_empty_when_b_ends_before_a_starts() -> None:
    # offset +5 with B only 3s long: no overlap at all.
    eng, a, b = make_engine(offset_a=0.0, offset_b=5.0)
    a.duration, b.duration = 10.0, 3.0
    assert eng.master_range() == (0.0, 0.0)


def test_master_range_raises_when_unloaded() -> None:
    eng, _, b = make_engine()
    b.duration = None
    with pytest.raises(SyncError):
        eng.master_range()


def test_seek_master_coarse_uses_fast_seeks() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)
    m = eng.seek_master(3.0, exact=False)
    assert m == pytest.approx(3.0)
    assert a.fast_seeks[-1] == pytest.approx(3.0)
    assert b.fast_seeks[-1] == pytest.approx(6.0)
    assert a.seeks == [] and b.seeks == []


def test_force_resync_aligns_b_to_a() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)
    a.time_pos, b.time_pos = 4.0, 9.9
    eng.force_resync()
    assert b.seeks[-1] == pytest.approx(7.0)  # 4.0 + offset 3.0


def test_force_resync_clamps_to_b_duration() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)  # offset +3
    a.time_pos, b.time_pos = 9.0, 1.0  # target 12 > duration 10
    eng.force_resync()
    assert b.seeks[-1] == pytest.approx(10.0)


def test_force_resync_clamps_to_zero() -> None:
    eng, a, b = make_engine(offset_a=5.0, offset_b=3.0)  # offset -2
    a.time_pos, b.time_pos = 0.5, 1.0  # target -1.5 < 0
    eng.force_resync()
    assert b.seeks[-1] == pytest.approx(0.0)


def test_force_resync_raises_when_unloaded() -> None:
    eng, a, _ = make_engine()
    a.time_pos = None
    with pytest.raises(SyncError):
        eng.force_resync()


# -- resync_if_needed ---------------------------------------------------------


def test_resync_if_needed_within_gate_does_not_seek() -> None:
    # Gate is 1.2 frames of reference fps 100 = 12ms; 8ms stays under.
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.008
    assert eng.resync_if_needed() is False
    assert b.seeks == []


def test_resync_if_needed_beyond_gate_seeks() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.05  # 50ms > 12ms gate
    assert eng.resync_if_needed() is True
    assert b.seeks[-1] == pytest.approx(5.0)


def test_resync_if_needed_none_when_unloaded() -> None:
    eng, a, b = make_engine()
    a.time_pos = None
    assert eng.resync_if_needed() is False
    assert b.seeks == []


def test_resync_if_needed_seeks_through_offset_basis() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)  # offset +3
    a.time_pos, b.time_pos = 4.0, 7.04  # 40ms drift through the offset
    assert eng.resync_if_needed() is True
    assert b.seeks[-1] == pytest.approx(7.0)  # 4.0 + offset 3.0


def test_resync_if_needed_uses_reference_fps_gate() -> None:
    # A at 25fps -> gate 48ms; a 30ms drift seeks at 100fps but not here.
    eng, a, b = make_engine()
    a.fps = 25.0
    a.time_pos, b.time_pos = 5.0, 5.03
    assert eng.resync_if_needed() is False
    assert b.seeks == []


def test_view_time_mapping() -> None:
    eng, _, _ = make_engine(offset_a=2.0, offset_b=5.0)
    assert eng.view_time("a", 1.0) == 1.0
    assert eng.view_time("b", 1.0) == pytest.approx(4.0)


def test_step_advances_one_reference_frame() -> None:
    eng, a, b = make_engine()
    a.time_pos = 5.0
    eng.step(1)
    assert a.seeks[-1] == pytest.approx(5.01)  # 1 / 100fps
    assert b.seeks[-1] == pytest.approx(5.01)
    assert a.paused and b.paused


def test_step_backwards() -> None:
    eng, a, _ = make_engine()
    a.time_pos = 5.0
    eng.step(-2)
    assert a.seeks[-1] == pytest.approx(4.98)


def test_drift_sign() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.10
    assert eng.drift() == pytest.approx(-0.10)  # B ahead


def test_drift_accounts_for_offset() -> None:
    # offset = +3: aligned frames sit 3s apart, drift must be zero there.
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)
    a.time_pos, b.time_pos = 4.0, 7.0
    assert eng.drift() == pytest.approx(0.0)
    a.time_pos, b.time_pos = 4.0, 7.5
    assert eng.drift() == pytest.approx(-0.5)


def test_drift_none_when_unloaded() -> None:
    eng, a, _ = make_engine()
    a.time_pos = None
    assert eng.drift() is None


def test_regulate_within_deadband_keeps_base_speed() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.001  # 1ms < deadband (0.3 frame @100fps = 3ms)
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.0)
    assert b.seeks == []


def test_regulate_speeds_up_b_when_behind() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 4.98  # drift +20ms > deadband (1 frame @100fps = 10ms)
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.0 * (1 + 0.02 / 0.3))  # +6.67%
    assert b.seeks == []  # never seeks during playback


def test_regulate_slows_down_b_when_ahead() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.02  # drift -20ms
    eng.regulate(2.0)
    assert b.speed == pytest.approx(2.0 * (1 - 0.02 / 0.3))


def test_regulate_huge_drift_nudges_max_without_seeking() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 4.0  # drift +1s: saturate at +10%
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.1)
    assert b.seeks == []  # hard correction only happens when paused, not here


def test_regulate_huge_negative_drift_clamps_to_minus_10pct() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 6.0  # drift -1s: saturate at -10%
    eng.regulate(2.0)
    assert b.speed == pytest.approx(2.0 * 0.9)
    assert b.seeks == []


def test_regulate_no_correction_when_aligned_after_sync() -> None:
    eng, a, b = make_engine(offset_a=2.0, offset_b=5.0)
    a.time_pos, b.time_pos = 4.0, 7.0  # exactly aligned through offset 3
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.0)
    assert len(b.speeds) == 1  # only the initial base-speed write


def test_regulate_ewma_smooths_second_sample_and_returns_drift() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.015  # raw drift +15ms
    first = eng.regulate(1.0)
    assert first == pytest.approx(0.015)
    assert b.speed == pytest.approx(1.0 + 0.015 / 0.3)
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.03  # raw drift +30ms
    second = eng.regulate(1.0)
    # Smoothed: 0.7*0.015 + 0.3*0.03 = 0.0195 (raw would give 0.03 -> 1.1)
    assert second == pytest.approx(0.0195)
    assert b.speed == pytest.approx(1.0 + 0.0195 / 0.3)


def test_regulate_deadband_exact_one_frame_is_no_correction() -> None:
    # fps 100 -> deadband exactly 0.01s; at the boundary no correction.
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.01
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.0)
    # Just past the boundary: corrected.
    eng2, a2, b2 = make_engine()
    a2.time_pos, b2.time_pos = 5.0, 5.0 - 0.011
    eng2.regulate(1.0)
    assert b2.speed == pytest.approx(1.0 + 0.011 / 0.3)


def test_regulate_uses_reference_fps_when_b_fps_missing() -> None:
    # b has no fps: deadband must come from A (50fps -> 20ms), not 25fps.
    eng, a, b = make_engine()
    a.fps, b.fps = 50.0, None
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.03  # 30ms drift
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.1)  # 30ms > 20ms deadband -> full nudge


def test_write_gating_dedupes_repeated_targets() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.015  # rate 5% -> 1.05
    eng.regulate(1.0)
    eng.regulate(1.0)  # same target 1.05: skipped
    assert b.speeds == [pytest.approx(1.05)]
    a.time_pos, b.time_pos = 5.0, 5.0  # ewma decays: 0.0105 still nudged
    eng.regulate(1.0)
    eng.regulate(1.0)  # ewma 0.00735 inside deadband: base written
    assert b.speeds == [
        pytest.approx(1.05),
        pytest.approx(1.0 + 0.0105 / 0.3),
        pytest.approx(1.0),
    ]


def test_sync_here_resets_regulate_state() -> None:
    # A stale EWMA must not leak into the new sync basis.
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.015
    eng.regulate(1.0)  # ewma now 0.015, speed 1.05
    a.time_pos, b.time_pos = 5.0, 4.985
    eng.sync_here()  # re-anchor on the offset views
    a.time_pos, b.time_pos = 6.0, 6.0 - 0.015  # exactly aligned now
    eng.regulate(1.0)
    # Without the reset the stale ewma (0.0105 smoothed) would nudge to 1.035.
    assert b.speed == pytest.approx(1.0)


def test_clear_sync_resets_regulate_state() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.0 - 0.015
    eng.regulate(1.0)
    eng.clear_sync()
    a.time_pos, b.time_pos = 6.0, 6.0  # aligned with offset 0
    eng.regulate(1.0)
    assert b.speed == pytest.approx(1.0)


def test_regulate_none_when_unloaded() -> None:
    eng, a, _ = make_engine()
    a.time_pos = None
    assert eng.regulate(1.0) is None


def test_regulate_skips_repeated_identical_writes() -> None:
    eng, a, b = make_engine()
    a.time_pos, b.time_pos = 5.0, 5.001  # within deadband
    eng.regulate(1.0)
    eng.regulate(1.0)
    eng.regulate(1.0)
    assert len(b.speeds) == 1  # first write applied, repeats skipped


def test_master_time_raises_when_unloaded() -> None:
    eng, a, _ = make_engine()
    a.time_pos = None
    with pytest.raises(SyncError):
        eng.master_time()


def test_ready_requires_both_durations() -> None:
    eng, a, b = make_engine()
    assert eng.ready
    b.duration = None
    assert not eng.ready


def test_ready_rejects_zero_duration() -> None:
    eng, a, _ = make_engine()
    a.duration = 0.0
    assert not eng.ready


def test_reference_fps_falls_back_to_25() -> None:
    eng, a, _ = make_engine()
    a.fps = None
    assert eng.reference_fps == pytest.approx(25.0)
