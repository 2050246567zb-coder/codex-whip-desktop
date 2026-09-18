from dataclasses import replace
import pytest
from codex_whip.calibration import DetectorProfile
from codex_whip.whip_sensitivity import WhipSensitivity, scaled_profile
from codex_whip.settings import Settings, load_settings


def test_strength_is_monotonic_bounded_and_keeps_safety_gates():
    base = DetectorProfile()
    profiles = [scaled_profile(base, p) for p in (60, 100, 140, 180)]
    for name in ('start_gyro_dps', 'confirm_gyro_dps', 'start_dynamic_accel_g',
                 'confirm_dynamic_accel_g', 'minimum_angular_travel_deg'):
        values = [getattr(p, name) for p in profiles]
        assert values == sorted(values, reverse=True)
    for p in profiles:
        assert p.confirm_samples == base.confirm_samples
        assert p.minimum_duration_ms == base.minimum_duration_ms
        assert p.minimum_dominant_axis_ratio == base.minimum_dominant_axis_ratio
        assert p.cooldown_ms == 400
    assert scaled_profile(base, 100) == base
    with pytest.raises(ValueError):
        scaled_profile(base, float('nan'))


def test_user_minimum_thresholds_remain_valid():
    base = replace(DetectorProfile(), start_gyro_dps=100, confirm_gyro_dps=150,
                   minimum_angular_travel_deg=50)
    high = scaled_profile(base, 180)
    assert high.start_gyro_dps == 100
    assert high.confirm_gyro_dps == 150
    assert high.minimum_angular_travel_deg < 50


def test_reopen_does_not_compound_and_advanced_change_rebases(tmp_path):
    path = tmp_path/'sensitivity.json'
    base = DetectorProfile()
    state = WhipSensitivity(base, path)
    state.save(150)
    current = scaled_profile(base, 150)
    reopened = WhipSensitivity(current, path)
    assert reopened.percent == 150
    assert scaled_profile(reopened.base, 100) == base
    edited = replace(current, minimum_angular_travel_deg=90)
    rebound = WhipSensitivity(edited, path)
    assert rebound.percent == 100
    assert rebound.base == edited


def test_default_desktop_interval_matches_board(tmp_path):
    assert Settings().events.minimum_interval_seconds == .4
    config = tmp_path/'config.toml'
    config.write_text('', encoding='utf-8')
    assert load_settings(config).events.minimum_interval_seconds == .4
