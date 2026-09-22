"""Static contract checks for the old XIAO's ST hardware tap engine."""

from pathlib import Path

from codex_whip.gui import CodexWhipWindow
from codex_whip.voice import VoiceSettings


ROOT = Path(__file__).resolve().parents[2]
SOURCE = (ROOT / "firmware/codex_whip/codex_whip.ino").read_text(encoding="utf-8")


def test_xiao_firmware_uses_one_second_st_double_tap_only() -> None:
    assert 'kFirmwareVersion[] = "0.8.0"' in SOURCE
    assert "writeImuRegisterVerified(kTapDurationRegister, 0xDF)" in SOURCE
    assert 'command.startsWith("TAPCFG,")' in SOURCE
    assert 'sendLine("TAP2," + String(now)' in SOURCE
    assert "kTapRawTailMs" not in SOURCE


def test_minimum_setting_prefers_saved_hardware_threshold() -> None:
    calibrated = VoiceSettings(
        tap_force_calibrated=True,
        tap_light_g=3.81,
        tap_heavy_g=7.07,
    )
    assert CodexWhipWindow._hardware_tap_minimum(calibrated) == 3.81
    assert CodexWhipWindow._hardware_tap_minimum(VoiceSettings()) == 1.25
