from dataclasses import replace
import math

import pytest

from codex_whip.models import RawMotionBatch, RawMotionFrame
from codex_whip.tap_calibration import ForceTapDetector, TapRangeCapture, MIN_TAP_IMPACT_G
from codex_whip.voice import VoiceModule, VoiceSettings, VoiceSettingsStore


def pair(start=0, strength=2, axis=(0, 0, 1), spacing=300):
    for dt in range(0, 1400, 10):
        pulse = strength if dt in (300, 310, 300 + spacing, 310 + spacing) else 0
        yield RawMotionFrame(
            start + dt, 0, 0, 0,
            axis[0] * pulse, axis[1] * pulse, 1 + axis[2] * pulse,
        )


def batch(frames):
    frames = tuple(frames)
    return RawMotionBatch(1, frames[0].timestamp_ms, frames)


def ranged_settings(minimum=.7, maximum=4.0, *, enabled=True):
    return VoiceSettings(
        enabled=enabled,
        tap_force_calibrated=True,
        tap_light_g=minimum,
        tap_heavy_g=maximum,
        impact_dynamic_accel_g=minimum,
        max_tap_gyro_dps=2000,
        min_interval_ms=150,
        max_interval_ms=700,
        pre_still_ms=120,
        settle_ms=50,
        max_pulse_ms=180,
    )


def module(tmp_path, settings=None):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    store.update(settings or ranged_settings())
    emitted = []
    voice = VoiceModule(store, lambda *event: emitted.append(event))
    return voice, emitted


@pytest.mark.parametrize('axis', [
    (0, 0, 1), (1, 0, 0), (0, 1, 0),
    (2**-.5, 0, 2**-.5), (0, 0, -1),
])
@pytest.mark.parametrize('strength', [MIN_TAP_IMPACT_G, 2.0, 4.0])
def test_two_impacts_inside_force_range_trigger_in_any_direction(axis, strength):
    detector = ForceTapDetector(ranged_settings())
    assert detector.feed_batch(batch(pair(strength=strength, axis=axis))) is not None


def test_detector_reanchors_after_handle_orientation_changes():
    frames = []
    timestamp = 0
    # Rotate from Z-up to X-up, then hold the new grip briefly.
    for index in range(40):
        angle = math.pi / 2 * index / 39
        frames.append(RawMotionFrame(
            timestamp, 70, 20, 10, math.sin(angle), 0, math.cos(angle)))
        timestamp += 10
    for _ in range(40):
        frames.append(RawMotionFrame(timestamp, 0, 0, 0, 1, 0, 0))
        timestamp += 10
    # Two short handle-bottom impacts in the new orientation, including a
    # gyro spike that must not make direction an implicit rejection rule.
    for offset in range(0, 700, 10):
        pulse = 2.0 if offset in (100, 110, 400, 410) else 0.0
        spike = 2600 if pulse else 0
        frames.append(RawMotionFrame(timestamp, spike, 0, 0, 1 + pulse, 0, 0))
        timestamp += 10
    detector = ForceTapDetector(ranged_settings())
    assert detector.feed_batch(batch(frames)) is not None


@pytest.mark.parametrize('strength', [.4, 4.5, 8.0])
def test_both_impacts_must_be_inside_selected_force_range(strength):
    detector = ForceTapDetector(ranged_settings())
    assert detector.feed_batch(batch(pair(strength=strength))) is None


def test_mixed_pair_is_rejected_when_one_impact_exceeds_maximum():
    frames = list(pair(strength=2))
    frames = [replace(frame, accel_z_g=6.0)
              if frame.timestamp_ms in (600, 610) else frame for frame in frames]
    assert ForceTapDetector(ranged_settings()).feed_batch(batch(frames)) is None


def test_capture_suggests_plus_or_minus_thirty_percent():
    capture = TapRangeCapture(ranged_settings())
    states = [state for frame in pair(strength=2)
              if (state := capture.feed(frame)) is not None]
    assert states[0]['live_strengths'] == pytest.approx((2.0,))
    assert states[-1]['stage'] == 'done'
    assert states[-1]['average_g'] == pytest.approx(2.0)
    assert states[-1]['suggested_min_g'] == pytest.approx(1.4)
    assert states[-1]['suggested_max_g'] == pytest.approx(2.6)


def test_capture_clamps_suggested_range_to_supported_limits():
    low = TapRangeCapture(ranged_settings())
    low_states = [state for frame in pair(strength=.2)
                  if (state := low.feed(frame)) is not None]
    assert low_states == []
    high = TapRangeCapture(ranged_settings())
    high_states = [state for frame in pair(strength=12)
                   if (state := high.feed(frame)) is not None]
    assert high_states[-1]['suggested_max_g'] == 12


def test_capture_ignores_ordinary_movement_below_physical_impact_floor():
    capture = TapRangeCapture(ranged_settings())
    frames = list(pair(strength=MIN_TAP_IMPACT_G - .05))
    assert all(capture.feed(frame) is None for frame in frames)
    assert capture.meter.strengths == ()


def test_runtime_floor_overrides_stale_too_low_saved_range():
    detector = ForceTapDetector(ranged_settings(.25, 4.0))
    assert detector.feed_batch(batch(pair(strength=MIN_TAP_IMPACT_G - .05))) is None
    assert detector.feed_batch(batch(pair(2000, strength=1.0))) is not None


def test_voice_capture_emits_readings_and_does_not_trigger_recording(tmp_path):
    voice, emitted = module(tmp_path)
    voice.start_force_calibration()
    assert voice.feed_motion(batch(pair(strength=2))) == (False, True)
    states = [payload for kind, payload in emitted if kind == 'tap_range_capture']
    assert states[-1]['stage'] == 'done'
    assert states[-1]['suggested_min_g'] == 1.4
    assert not any(kind == 'voice_trigger' for kind, _payload in emitted)
    assert not voice.calibration_active


def test_runtime_raw_motion_never_runs_custom_double_tap_detector(tmp_path):
    voice, _ = module(tmp_path, ranged_settings(1.4, 2.6))
    assert voice.feed_motion(batch(pair(strength=2))) == (False, False)
    assert not voice.feed_motion(batch(pair(2000, strength=1.0)))[0]
    assert not voice.feed_motion(batch(pair(4000, strength=3.0)))[0]


def test_hardware_double_tap_is_the_only_runtime_trigger(tmp_path):
    voice, emitted = module(tmp_path, ranged_settings(1.4, 2.6))

    assert voice.handle_hardware_double_tap(670)
    event = next(payload for kind, payload in emitted if kind == 'voice_trigger')
    assert event.first_peak_dynamic_accel_g == 0.0
    assert event.second_at_ms == 670
    assert any(kind == 'log' and 'ST 状态机' in str(payload)
               for kind, payload in emitted)


def test_hardware_event_needs_no_desktop_force_or_interval_validation(tmp_path):
    voice, emitted = module(tmp_path, ranged_settings(2.5, 4.0))

    assert voice.handle_hardware_double_tap(670)
    assert any(kind == 'voice_trigger' for kind, _payload in emitted)


def test_hardware_event_deduplicates_repeated_notification(tmp_path):
    voice, emitted = module(tmp_path, ranged_settings(1.4, 2.6))
    assert voice.handle_hardware_double_tap(670)
    triggers_before = sum(kind == 'voice_trigger' for kind, _ in emitted)

    assert not voice.handle_hardware_double_tap(670)
    assert sum(kind == 'voice_trigger' for kind, _ in emitted) == triggers_before


def test_single_heavy_impact_with_fast_rebounds_does_not_become_double_tap():
    detector = ForceTapDetector(ranged_settings(.5, 10))
    frames = [RawMotionFrame(t, 0, 0, 0, 0, 0, 1 + (8 if t in (300, 340, 380) else 0))
              for t in range(0, 1800, 10)]
    assert detector.feed_batch(batch(frames)) is None


def test_sustained_rotation_and_broad_acceleration_are_not_taps():
    detector = ForceTapDetector(ranged_settings(.5, 10))
    frames = [RawMotionFrame(t, 500, 0, 0, 0, 0,
                            1 + (4 if 300 <= t < 550 or 700 <= t < 900 else 0))
              for t in range(0, 1800, 10)]
    assert detector.feed_batch(batch(frames)) is None


def test_transport_gap_duplicates_and_invalid_samples_cannot_join_impacts():
    detector = ForceTapDetector(ranged_settings(.5, 10))
    frames = list(pair())
    frames = [frame for frame in frames if not 400 <= frame.timestamp_ms < 590]
    assert detector.feed_batch(batch(frames)) is None
    assert detector.feed_batch(batch([frames[-1]] * 500)) is None
    detector._feed_frame(RawMotionFrame(2300, 0, 0, 0, math.nan, 0, 1))
    assert detector._gravity is None


def test_force_range_validation():
    with pytest.raises(ValueError):
        ranged_settings(.2, 2).validated()
    # Maximum is a legacy storage field and no longer constrains hardware.
    assert ranged_settings(2, 2).validated().tap_light_g == 2
    with pytest.raises(ValueError):
        ranged_settings(13, 2).validated()
    assert ranged_settings(2, 12.1).validated().tap_heavy_g == 12.1
