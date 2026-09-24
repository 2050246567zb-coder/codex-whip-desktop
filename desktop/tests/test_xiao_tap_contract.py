"""Static contract checks for the old XIAO's ST hardware tap engine."""

from pathlib import Path

from codex_whip.gui import CodexWhipWindow
from codex_whip.voice import VoiceSettings


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "firmware/codex_whip/codex_whip.ino").read_text(encoding="utf-8")


def test_xiao_firmware_uses_one_second_st_double_tap_only() -> None:
    assert 'kFirmwareVersion[] = "0.8.3"' in SOURCE
    assert "writeImuRegisterVerified(kTapDurationRegister, 0xDF)" in SOURCE
    assert 'command.startsWith("TAPCFG,")' in SOURCE
    assert 'sendLine("TAP2," + String(now)' in SOURCE
    assert "kTapRawTailMs" not in SOURCE


def test_offline_idle_keeps_discoverable_at_one_second_interval() -> None:
    assert "kOfflineSleepAdvertisingUnits = 1600" in SOURCE
    assert "else advertiseWhileOfflineSleeping();" in SOURCE
    assert "Bluefruit.Advertising.setInterval(kOfflineSleepAdvertisingUnits," in SOURCE
    assert "else {\n    advertise();\n  }" in SOURCE


def test_minimum_setting_prefers_saved_hardware_threshold() -> None:
    calibrated = VoiceSettings(
        tap_force_calibrated=True,
        tap_light_g=3.81,
        tap_heavy_g=7.07,
    )
    assert CodexWhipWindow._hardware_tap_minimum(calibrated) == 3.81
    assert CodexWhipWindow._hardware_tap_minimum(VoiceSettings()) == 1.0


def test_small_ble_mtu_uses_transport_fragmentation_instead_of_aborting() -> None:
    sketch = SOURCE
    assert 'kVoiceFallbackPcmSamples = 300' in sketch
    assert 'voicePcmSamples = kVoiceFallbackPcmSamples' in sketch
    assert 'kVoiceStreamingMtu = 64' in sketch
    assert 'connection->requestMtuExchange(247)' in sketch
    assert 'connection->getConnectionInterval() <= activeHost.maxInterval' in sketch
    assert 'sendLine("VOICE,ERROR,LINK_SPEED")' in sketch
    assert 'VOICE,END," + String(voiceSession) + ",0,LINK_MTU' not in sketch
    assert 'Bluefruit.configPrphBandwidth(BANDWIDTH_MAX)' in sketch


def test_unusable_saved_hardware_threshold_is_capped() -> None:
    stale = VoiceSettings(
        tap_force_calibrated=True,
        tap_light_g=9.5,
        tap_heavy_g=12.0,
        impact_dynamic_accel_g=9.5,
    )
    assert CodexWhipWindow._hardware_tap_minimum(stale) == 4.0
