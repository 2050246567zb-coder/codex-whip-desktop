import json
from codex_whip.interface_state import InterfacePreferences, audio_display_level
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
    assert set(json.loads(path.read_text())) == {"setup_complete", "reduce_motion"}


def test_first_use_and_corrupt_preferences_are_safe(tmp_path):
    path = tmp_path / "interface-preferences.json"
    assert not InterfacePreferences.load(path, already_calibrated=False).setup_complete
    path.write_text("[]")
    assert not InterfacePreferences.load(path, already_calibrated=False).setup_complete
    path.write_text('{"setup_complete":"false"}')
    assert not InterfacePreferences.load(path, already_calibrated=True).setup_complete


def test_meter_is_real_pcm_rms_and_invalid_packets_are_silent():
    quiet = AudioChunk(1, 0, 1, 0, 0, b"")
    loud = AudioChunk(1, 1, 1, 10000, 0, b"")
    assert audio_display_level(quiet) == 0
    assert 0.5 < audio_display_level(loud) <= 1
    assert audio_display_level(AudioChunk(1, 2, 9999, 0, 1000, b"")) == 0
