import json

import pytest


def test_feedback_switches_and_zero_interval_roundtrip(tmp_path):
    from codex_whip.visual_settings import VisualSettingsStore, VisualSettings
    path = tmp_path/'visual.json'
    store = VisualSettingsStore(path)
    store.update(VisualSettings(strikes_per_wound=0, wounds_enabled=False, sound_enabled=False))
    restored = VisualSettingsStore(path).settings
    assert restored.strikes_per_wound == 0
    assert not restored.wounds_enabled
    assert not restored.sound_enabled

from codex_whip.visual_settings import (
    VisualSettings,
    VisualSettingsStore,
    load_visual_settings,
)


def test_visual_settings_round_trip_and_default(tmp_path) -> None:
    path = tmp_path / "visual-settings.json"
    assert load_visual_settings(path) == VisualSettings()

    store = VisualSettingsStore(path)
    store.update(VisualSettings(strikes_per_wound=5))

    assert store.settings == VisualSettings(strikes_per_wound=5)
    assert load_visual_settings(path) == VisualSettings(strikes_per_wound=5)


def test_visual_settings_round_trip_red_eye_options(tmp_path) -> None:
    path = tmp_path / "visual-settings.json"
    expected = VisualSettings(
        strikes_per_wound=3,
        scare_enabled=False,
        scare_hotkey="ctrl+shift+f8",
        scare_blackout_ms=800,
        scare_eyes_ms=2200,
    )
    store = VisualSettingsStore(path)
    store.update(expected)

    assert load_visual_settings(path) == expected
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 3


def test_visual_settings_migrates_schema_one(tmp_path) -> None:
    path = tmp_path / "visual-settings.json"
    path.write_text(
        json.dumps({"schema_version": 1, "strikes_per_wound": 7}),
        encoding="utf-8",
    )

    loaded = load_visual_settings(path)

    assert loaded.strikes_per_wound == 7
    assert loaded.scare_enabled is True
    assert loaded.scare_hotkey == "ctrl+alt+shift+x"
    assert loaded.scare_blackout_ms == 2000
    assert loaded.scare_eyes_ms == 3000


def test_visual_settings_migrates_old_red_eye_timing(tmp_path) -> None:
    path = tmp_path / "visual-settings.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "strikes_per_wound": 4,
                "scare_enabled": True,
                "scare_hotkey": "ctrl+shift+f8",
                "scare_blackout_ms": 1000,
                "scare_eyes_ms": 1500,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_visual_settings(path)

    assert loaded.strikes_per_wound == 4
    assert loaded.scare_hotkey == "ctrl+shift+f8"
    assert loaded.scare_blackout_ms == 2000
    assert loaded.scare_eyes_ms == 3000


@pytest.mark.parametrize("value", (-1, 101))
def test_visual_settings_reject_invalid_wound_intervals(value: int) -> None:
    with pytest.raises(ValueError):
        VisualSettings(strikes_per_wound=value).validated()


@pytest.mark.parametrize(
    ("field", "value"),
    (("scare_blackout_ms", 199), ("scare_blackout_ms", 5001),
     ("scare_eyes_ms", 299), ("scare_eyes_ms", 10001)),
)
def test_visual_settings_reject_invalid_red_eye_timings(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        VisualSettings(**{field: value}).validated()
