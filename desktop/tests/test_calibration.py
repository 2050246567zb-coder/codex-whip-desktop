import json

import pytest

from codex_whip.calibration import (
    DetectorProfile,
    LearningSample,
    learn_profile,
    load_profile,
    profile_score,
    sample_averages,
    save_profile,
)


def sample(
    sequence: int,
    gyro: float,
    dynamic_accel: float,
    duration: int,
    travel: float,
    axis: float,
    gap: int,
) -> LearningSample:
    return LearningSample(
        sequence=sequence,
        result="CAPTURED",
        peak_gyro_dps=gyro,
        peak_dynamic_accel_g=dynamic_accel,
        duration_ms=duration,
        angular_travel_deg=travel,
        direction_consistency=0.4,
        dominant_axis_ratio=axis,
        peak_gap_ms=gap,
        peak_jerk_gps=200.0,
    )


def test_profile_commands_are_short_and_complete() -> None:
    commands = DetectorProfile().commands()

    assert len(commands) == 14
    assert commands[0] == "CFG,SG,546"
    assert commands[-1] == "CFG,CD,700"
    assert all(len(command.encode("ascii")) < 20 for command in commands)


def test_profile_validation_rejects_inconsistent_thresholds() -> None:
    with pytest.raises(ValueError, match="确认角速度"):
        DetectorProfile(start_gyro_dps=900, confirm_gyro_dps=800).validated()


def test_profile_round_trip_and_corrupt_fallback(tmp_path) -> None:
    path = tmp_path / "profile.json"
    profile = DetectorProfile(start_gyro_dps=420.0, confirm_gyro_dps=700.0)

    save_profile(profile, path)
    assert load_profile(path) == profile
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 2

    path.write_text("not-json", encoding="utf-8")
    assert load_profile(path) == DetectorProfile()


def test_old_automatic_learning_profile_is_invalidated(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile": {
                    "start_gyro_dps": 150.0,
                    "confirm_gyro_dps": 392.2,
                    "minimum_angular_travel_deg": 8.0,
                },
            }
        ),
        encoding="utf-8",
    )

    assert load_profile(path) == DetectorProfile()


def test_learning_keeps_whips_and_rejects_desk_impacts() -> None:
    positives = [
        sample(
            index,
            760 + index * 28,
            0.75 + index * 0.055,
            115 + index * 5,
            48 + index * 2.5,
            0.50 + index * 0.009,
            45 + index * 3,
        )
        for index in range(15)
    ]
    negatives = [
        sample(index, 240 + index * 25, 2.8 + index * 0.25, 90, 12, 0.35, 15)
        for index in range(5)
    ]

    profile = learn_profile(positives, negatives)
    kept, false_positives = profile_score(profile, positives, negatives)

    assert kept >= 12
    assert false_positives == 0
    assert profile.start_gyro_dps < DetectorProfile().start_gyro_dps


def test_learning_requires_fifteen_positive_samples() -> None:
    with pytest.raises(ValueError, match="15"):
        learn_profile([sample(1, 900, 1.2, 130, 60, 0.6, 40)])


def test_sample_averages_are_reported() -> None:
    averages = sample_averages(
        [sample(1, 800, 1.0, 100, 50, 0.5, 20), sample(2, 1000, 2.0, 200, 70, 0.7, 40)]
    )

    assert averages["peak_gyro_dps"] == 900
    assert averages["peak_dynamic_accel_g"] == 1.5
