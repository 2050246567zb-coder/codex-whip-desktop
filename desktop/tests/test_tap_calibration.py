from dataclasses import replace
import math
import pytest

from codex_whip.models import RawMotionBatch, RawMotionFrame
from codex_whip.voice import VoiceModule, VoiceSettingsStore, VoiceSettings
from codex_whip.tap_calibration import ForceTapDetector


def pair(start=0, strength=1, axis=(0, 0, 1), spacing=300):
    for dt in range(0, 2200, 10):
        pulse = strength if dt in (300, 310, 300+spacing, 310+spacing) else 0
        yield RawMotionFrame(start+dt, 0, 0, 0,
                             axis[0]*pulse, axis[1]*pulse, 1+axis[2]*pulse)


def batch(frames):
    frames = tuple(frames)
    return RawMotionBatch(1, frames[0].timestamp_ms, frames)


def module(tmp_path, enabled=True):
    store = VoiceSettingsStore(tmp_path/'voice.json')
    store.update(VoiceSettings(enabled=enabled, impact_dynamic_accel_g=8))
    emitted = []
    voice = VoiceModule(store, lambda *e: emitted.append(e))
    return voice, emitted


def calibrate(voice):
    voice.start_force_calibration()
    assert voice.feed_motion(batch(pair(strength=.8))) == (False, True)
    assert voice._force_calibration.stage == 'heavy'
    assert voice.feed_motion(batch(pair(2200, 4))) == (False, True)
    assert voice._force_calibration.stage == 'test'


def test_two_pairs_auto_advance_without_old_threshold_and_save_only_on_confirm(tmp_path):
    voice, emitted = module(tmp_path)
    old = voice.store.path.read_bytes()
    calibrate(voice)
    assert voice.store.path.read_bytes() == old
    voice.feed_motion(batch(pair(4400, 2)))
    assert voice._force_calibration.accepted == 1
    assert not any(k == 'voice_trigger' for k, _ in emitted)
    voice.save_force_calibration()
    assert not voice.calibration_active
    assert voice.store.settings.tap_force_calibrated
    assert voice.store.settings.impact_dynamic_accel_g == pytest.approx(.52)
    restored = VoiceModule(VoiceSettingsStore(voice.store.path), lambda *e: None)
    assert isinstance(restored.detector, ForceTapDetector)


@pytest.mark.parametrize('axis', [(0,0,1), (1,0,0), (0,1,0), (2**-.5,0,2**-.5), (0,0,-1)])
@pytest.mark.parametrize('strength', [.8, 2, 8, 12])
def test_changed_strength_and_direction_accepted_in_test_and_live(tmp_path, axis, strength):
    voice, _ = module(tmp_path)
    calibrate(voice)
    voice.feed_motion(batch(pair(4400, strength, axis)))
    assert voice._force_calibration.accepted == 1
    voice.save_force_calibration()
    assert voice.feed_motion(batch(pair(6600, strength, axis)))[0]


def test_cancel_restart_and_disabled_voice_preserve_saved_settings(tmp_path):
    voice, emitted = module(tmp_path, enabled=False)
    old = voice.store.path.read_bytes()
    calibrate(voice)
    voice.start_force_calibration()
    assert voice._force_calibration.stage == 'light'
    voice.cancel_calibration()
    assert not voice.calibration_active
    assert voice.store.path.read_bytes() == old
    assert voice.feed_motion(batch(pair(4400, 4))) == (False, False)


def test_single_heavy_impact_with_fast_rebounds_does_not_become_double_tap():
    detector = ForceTapDetector(VoiceSettings(impact_dynamic_accel_g=.5))
    frames = [RawMotionFrame(t, 0,0,0, 0,0, 1+(8 if t in (300,340,380) else 0))
              for t in range(0, 1800, 10)]
    assert detector.feed_batch(batch(frames)) is None


def test_sustained_rotation_and_broad_acceleration_are_not_taps():
    detector = ForceTapDetector(VoiceSettings(impact_dynamic_accel_g=.5))
    frames = [RawMotionFrame(t, 500,0,0, 0,0, 1+(4 if 300 <= t < 550 or 700 <= t < 900 else 0))
              for t in range(0, 1800, 10)]
    assert detector.feed_batch(batch(frames)) is None


def test_transport_gap_duplicates_and_invalid_samples_cannot_join_two_impacts():
    detector = ForceTapDetector(VoiceSettings(impact_dynamic_accel_g=.5))
    frames = list(pair())
    frames = [f for f in frames if not 400 <= f.timestamp_ms < 590]
    assert detector.feed_batch(batch(frames)) is None
    assert detector.feed_batch(batch([frames[-1]]*500)) is None
    detector._feed_frame(RawMotionFrame(2300, 0,0,0, math.nan,0,1))
    assert detector._gravity is None


def test_below_threshold_test_reports_rejection(tmp_path):
    voice, emitted = module(tmp_path)
    calibrate(voice)
    voice.feed_motion(batch(pair(4400, .3)))
    assert voice._force_calibration.accepted == 0
    assert any(k == 'tap_calibration_state' and '未通过' in p['detail'] for k,p in emitted)


def test_failed_save_keeps_draft_and_old_runtime(tmp_path, monkeypatch):
    voice, _ = module(tmp_path)
    calibrate(voice)
    def fail(_):
        raise OSError('disk full')
    monkeypatch.setattr(voice.store, 'update', fail)
    with pytest.raises(OSError):
        voice.save_force_calibration()
    assert voice.calibration_active
    assert not voice.store.settings.tap_force_calibrated


def test_new_resting_orientation_relearns_gravity_without_recalibration():
    detector = ForceTapDetector(VoiceSettings(impact_dynamic_accel_g=.5, pre_still_ms=120))
    detector.feed_batch(batch(pair(strength=0)))
    frames = [RawMotionFrame(2200+t, 0,0,0, 1,0,0) for t in range(0,500,10)]
    assert detector.feed_batch(batch(frames)) is None
    frames = [replace(f, accel_x_g=1+f.accel_z_g-1, accel_z_g=0)
              for f in pair(2700, 3)]
    assert detector.feed_batch(batch(frames)) is not None


def test_calibration_blocks_all_strike_sources_even_with_voice_disabled(tmp_path):
    import asyncio
    import threading
    from codex_whip.gui import GuiEventProcessor
    from codex_whip.settings import Settings
    from codex_whip.models import WhipEvent
    voice, _ = module(tmp_path, enabled=False)
    voice.start_force_calibration()
    emitted = []
    armed = threading.Event()
    armed.set()
    processor = GuiEventProcessor(Settings(), armed, lambda *e: emitted.append(e), voice_module=voice)
    for source in ('device', 'mouse', 'simulation', 'motion_v3'):
        asyncio.run(processor.handle(WhipEvent(1, 500, 3, 120), source=source))
    assert not any(k in ('whip','send_result','send_error') for k, _ in emitted)


@pytest.mark.parametrize('limit', [200, 400, 600, 800, 1000])
def test_interval_slider_limits_apply_to_capture_test_and_saved_runtime(tmp_path, limit):
    voice, _ = module(tmp_path)
    voice.start_force_calibration(limit)
    voice.feed_motion(batch(pair(strength=.8, spacing=limit)))
    assert voice._force_calibration.stage == 'heavy'
    voice.feed_motion(batch(pair(3200, 4, spacing=limit)))
    assert voice._force_calibration.stage == 'test'
    voice.feed_motion(batch(pair(6400, 2, spacing=limit)))
    assert voice._force_calibration.accepted == 1
    voice.save_force_calibration()
    assert voice.store.settings.max_interval_ms == limit
    assert voice.feed_motion(batch(pair(9600, 2, spacing=limit)))[0]
    assert not voice.feed_motion(batch(pair(12800, 2, spacing=limit+20)))[0]


def test_adjusting_draft_interval_does_not_save_or_lose_strength_samples(tmp_path):
    voice, _ = module(tmp_path)
    calibrate(voice)
    old = voice.store.path.read_bytes()
    samples = (voice._force_calibration.light, voice._force_calibration.heavy)
    voice.set_tap_interval(200)
    assert voice._force_calibration.stage == 'test'
    assert (voice._force_calibration.light, voice._force_calibration.heavy) == samples
    assert voice.store.path.read_bytes() == old
    voice.feed_motion(batch(pair(4400, 2, spacing=300)))
    assert voice._force_calibration.accepted == 0
    voice.set_tap_interval(1000)
    voice.feed_motion(batch(pair(6600, 2, spacing=800)))
    assert voice._force_calibration.accepted == 1
    voice.cancel_calibration()
    assert voice.store.path.read_bytes() == old


@pytest.mark.parametrize('limit', [199, 1001])
def test_interval_out_of_range_rejected(tmp_path, limit):
    voice, _ = module(tmp_path)
    voice.start_force_calibration()
    with pytest.raises(ValueError):
        voice.set_tap_interval(limit)
