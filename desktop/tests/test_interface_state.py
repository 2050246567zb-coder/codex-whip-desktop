import json
from codex_whip.interface_state import (
    InterfacePreferences, audio_display_level, initialize_fresh_preferences,
)
from codex_whip.models import AudioChunk


def test_preferences_only_save_interface_file(tmp_path):
    sensor = tmp_path / "mounting-profile.json"
    sensor.write_text("original sensor profile")
    path = tmp_path / "interface-preferences.json"
    preferences = InterfacePreferences.load(path, already_calibrated=True)
    assert preferences.setup_complete
    preferences.reduce_motion = True
    preferences.save(path)
    assert InterfacePreferences.load(path, already_calibrated=False) == preferences
    assert sensor.read_text() == "original sensor profile"
    assert set(json.loads(path.read_text())) == {"setup_complete", "reduce_motion", "send_enabled"}
    assert preferences.send_enabled is True
    preferences.send_enabled = False
    preferences.save(path)
    assert InterfacePreferences.load(path, already_calibrated=True).send_enabled is False


def test_first_use_and_corrupt_preferences_are_safe(tmp_path):
    path = tmp_path / "interface-preferences.json"
    assert not InterfacePreferences.load(path, already_calibrated=False).setup_complete
    path.write_text("[]")
    assert not InterfacePreferences.load(path, already_calibrated=False).setup_complete
    path.write_text('{"setup_complete":"false"}')
    assert not InterfacePreferences.load(path, already_calibrated=True).setup_complete
    assert InterfacePreferences.load(path, already_calibrated=True).send_enabled is True


def test_fresh_defaults_keep_tour_pending_and_preserve_existing_profile(tmp_path):
    assert initialize_fresh_preferences(tmp_path)
    path = tmp_path / "interface-preferences.json"
    assert InterfacePreferences.load(path, already_calibrated=True).setup_complete is False
    assert InterfacePreferences.load(path, already_calibrated=True).send_enabled is True
    path.write_text('{"setup_complete": true, "send_enabled": false}')
    assert not initialize_fresh_preferences(tmp_path)
    assert InterfacePreferences.load(path, already_calibrated=True).send_enabled is False


def test_first_use_still_starts_tour_when_logs_or_factory_settings_exist(tmp_path):
    from codex_whip.migration import bundled_factory_calibration_dir

    factory = bundled_factory_calibration_dir()
    assert factory is not None
    (tmp_path / "runtime.log").write_text("previous startup", encoding="utf-8")
    (tmp_path / "voice").mkdir()
    (tmp_path / "mounting-profile.json").write_bytes(
        (factory / "mounting-profile.json").read_bytes()
    )
    assert initialize_fresh_preferences(tmp_path)
    assert not InterfacePreferences.load(
        tmp_path / "interface-preferences.json", already_calibrated=True
    ).setup_complete


def test_legacy_personal_calibration_does_not_repeat_tour(tmp_path):
    from codex_whip.migration import bundled_factory_calibration_dir

    factory = bundled_factory_calibration_dir()
    assert factory is not None
    profile = json.loads((factory / "mounting-profile.json").read_text())
    profile["right_angle_deg"] += 5
    (tmp_path / "mounting-profile.json").write_text(json.dumps(profile))
    assert initialize_fresh_preferences(tmp_path)
    assert InterfacePreferences.load(
        tmp_path / "interface-preferences.json", already_calibrated=False
    ).setup_complete


def test_meter_is_real_pcm_rms_and_invalid_packets_are_silent():
    quiet = AudioChunk(1, 0, 1, 0, 0, b"")
    loud = AudioChunk(1, 1, 1, 10000, 0, b"")
    assert audio_display_level(quiet) == 0
    assert 0.5 < audio_display_level(loud) <= 1
    assert audio_display_level(AudioChunk(1, 2, 9999, 0, 1000, b"")) == 0
