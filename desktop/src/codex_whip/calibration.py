from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Iterable

from .paths import user_data_dir


POSITIVE_SAMPLE_COUNT = 15
NEGATIVE_SAMPLE_COUNT = 5
PROFILE_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class LearningSample:
    sequence: int
    result: str
    peak_gyro_dps: float
    peak_dynamic_accel_g: float
    duration_ms: int
    angular_travel_deg: float
    direction_consistency: float
    dominant_axis_ratio: float
    peak_gap_ms: int
    peak_jerk_gps: float


@dataclass(frozen=True, slots=True)
class DetectorProfile:
    start_gyro_dps: float = 546.0
    start_dynamic_accel_g: float = 0.885
    confirm_gyro_dps: float = 845.0
    confirm_dynamic_accel_g: float = 1.275
    hard_dynamic_accel_g: float = 2.38
    hard_accel_min_gyro_dps: float = 390.0
    confirm_samples: int = 3
    minimum_angular_travel_deg: float = 35.0
    minimum_direction_consistency: float = 0.0
    minimum_dominant_axis_ratio: float = 0.45
    maximum_peak_gap_ms: int = 180
    minimum_duration_ms: int = 40
    maximum_event_ms: int = 900
    cooldown_ms: int = 400

    def validated(self) -> DetectorProfile:
        limits: dict[str, tuple[float, float]] = {
            "start_gyro_dps": (100.0, 2500.0),
            "start_dynamic_accel_g": (0.1, 8.0),
            "confirm_gyro_dps": (150.0, 3500.0),
            "confirm_dynamic_accel_g": (0.1, 12.0),
            "hard_dynamic_accel_g": (0.2, 15.0),
            "hard_accel_min_gyro_dps": (100.0, 3000.0),
            "confirm_samples": (1.0, 8.0),
            "minimum_angular_travel_deg": (5.0, 500.0),
            "minimum_direction_consistency": (0.0, 1.0),
            "minimum_dominant_axis_ratio": (0.0, 1.0),
            "maximum_peak_gap_ms": (0.0, 800.0),
            "minimum_duration_ms": (10.0, 500.0),
            "maximum_event_ms": (200.0, 2000.0),
            "cooldown_ms": (200.0, 3000.0),
        }
        for name, (minimum, maximum) in limits.items():
            value = getattr(self, name)
            if not isinstance(value, int | float) or not math.isfinite(float(value)):
                raise ValueError(f"{name} 必须是有限数值")
            if not minimum <= float(value) <= maximum:
                raise ValueError(f"{name} 必须在 {minimum:g} 到 {maximum:g} 之间")
        if self.confirm_gyro_dps < self.start_gyro_dps:
            raise ValueError("确认角速度不能低于启动角速度")
        if self.confirm_dynamic_accel_g < self.start_dynamic_accel_g:
            raise ValueError("确认动态加速度不能低于启动动态加速度")
        if self.maximum_event_ms <= self.minimum_duration_ms:
            raise ValueError("动作最长时间必须大于最短时间")
        return self

    def commands(self) -> tuple[str, ...]:
        values = (
            ("SG", self.start_gyro_dps),
            ("SA", self.start_dynamic_accel_g),
            ("CG", self.confirm_gyro_dps),
            ("CA", self.confirm_dynamic_accel_g),
            ("HA", self.hard_dynamic_accel_g),
            ("HG", self.hard_accel_min_gyro_dps),
            ("CS", self.confirm_samples),
            ("AT", self.minimum_angular_travel_deg),
            ("DC", self.minimum_direction_consistency),
            ("AX", self.minimum_dominant_axis_ratio),
            ("PG", self.maximum_peak_gap_ms),
            ("MD", self.minimum_duration_ms),
            ("ME", self.maximum_event_ms),
            ("CD", self.cooldown_ms),
        )
        return tuple(f"CFG,{key},{_format_value(value)}" for key, value in values)


def _format_value(value: int | float) -> str:
    if isinstance(value, int):
        return str(value)
    return f"{value:.4f}".rstrip("0").rstrip(".")


def default_profile_path() -> Path:
    return user_data_dir() / "detector-profile.json"


def load_profile(path: Path | None = None) -> DetectorProfile:
    target = path or default_profile_path()
    if not target.is_file():
        return DetectorProfile()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("profile root must be an object")
        if raw.get("schema_version") != PROFILE_SCHEMA_VERSION:
            return DetectorProfile()
        values = raw.get("profile", raw)
        if not isinstance(values, dict):
            raise ValueError("profile must be an object")
        allowed = {field.name for field in fields(DetectorProfile)}
        profile = DetectorProfile(**{key: value for key, value in values.items() if key in allowed})
        return profile.validated()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return DetectorProfile()


def save_profile(profile: DetectorProfile, path: Path | None = None) -> Path:
    validated = profile.validated()
    target = path or default_profile_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    payload = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "profile": asdict(validated),
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(target)
    return target


def _percentile(values: Iterable[float], percentile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("至少需要一个样本")
    position = min(1.0, max(0.0, percentile)) * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _accepts(profile: DetectorProfile, sample: LearningSample) -> bool:
    confirmed = (
        sample.peak_gyro_dps >= profile.confirm_gyro_dps
        and sample.peak_dynamic_accel_g >= profile.confirm_dynamic_accel_g
    ) or (
        sample.peak_dynamic_accel_g >= profile.hard_dynamic_accel_g
        and sample.peak_gyro_dps >= profile.hard_accel_min_gyro_dps
    )
    return bool(
        confirmed
        and sample.duration_ms >= profile.minimum_duration_ms
        and sample.duration_ms <= profile.maximum_event_ms
        and sample.angular_travel_deg >= profile.minimum_angular_travel_deg
        and sample.direction_consistency >= profile.minimum_direction_consistency
        and sample.dominant_axis_ratio >= profile.minimum_dominant_axis_ratio
        and sample.peak_gap_ms <= profile.maximum_peak_gap_ms
    )


def profile_score(
    profile: DetectorProfile,
    positives: Iterable[LearningSample],
    negatives: Iterable[LearningSample],
) -> tuple[int, int]:
    positive_items = tuple(positives)
    negative_items = tuple(negatives)
    return (
        sum(_accepts(profile, sample) for sample in positive_items),
        sum(_accepts(profile, sample) for sample in negative_items),
    )


def learn_profile(
    positives: Iterable[LearningSample],
    negatives: Iterable[LearningSample] = (),
) -> DetectorProfile:
    positive_items = tuple(positives)
    negative_items = tuple(negatives)
    if len(positive_items) < POSITIVE_SAMPLE_COUNT:
        raise ValueError(f"需要 {POSITIVE_SAMPLE_COUNT} 次挥鞭样本")

    gyro_p10 = _percentile((item.peak_gyro_dps for item in positive_items), 0.10)
    gyro_p20 = _percentile((item.peak_gyro_dps for item in positive_items), 0.20)
    accel_p10 = _percentile(
        (item.peak_dynamic_accel_g for item in positive_items), 0.10
    )
    accel_p20 = _percentile(
        (item.peak_dynamic_accel_g for item in positive_items), 0.20
    )

    profile = DetectorProfile(
        start_gyro_dps=round(_clamp(gyro_p10 * 0.62, 150.0, 1200.0), 1),
        start_dynamic_accel_g=round(_clamp(accel_p10 * 0.58, 0.18, 3.0), 3),
        confirm_gyro_dps=round(_clamp(gyro_p20 * 0.76, 180.0, 2200.0), 1),
        confirm_dynamic_accel_g=round(
            _clamp(accel_p20 * 0.76, 0.22, 6.0), 3
        ),
        hard_dynamic_accel_g=round(
            _clamp(
                _percentile(
                    (item.peak_dynamic_accel_g for item in positive_items), 0.75
                )
                * 0.92,
                0.5,
                10.0,
            ),
            3,
        ),
        hard_accel_min_gyro_dps=round(
            _clamp(gyro_p10 * 0.55, 120.0, 1800.0), 1
        ),
        confirm_samples=2,
        minimum_angular_travel_deg=round(
            _clamp(
                _percentile(
                    (item.angular_travel_deg for item in positive_items), 0.10
                )
                * 0.72,
                8.0,
                240.0,
            ),
            1,
        ),
        minimum_direction_consistency=0.0,
        minimum_dominant_axis_ratio=round(
            _clamp(
                _percentile(
                    (item.dominant_axis_ratio for item in positive_items), 0.10
                )
                * 0.88,
                0.20,
                0.90,
            ),
            3,
        ),
        maximum_peak_gap_ms=round(
            _clamp(
                _percentile((item.peak_gap_ms for item in positive_items), 0.90)
                + 25.0,
                40.0,
                500.0,
            )
        ),
        minimum_duration_ms=round(
            _clamp(
                _percentile((item.duration_ms for item in positive_items), 0.10)
                * 0.65,
                20.0,
                180.0,
            )
        ),
        maximum_event_ms=round(
            _clamp(
                _percentile((item.duration_ms for item in positive_items), 0.95)
                * 1.35
                + 80.0,
                350.0,
                1500.0,
            )
        ),
        cooldown_ms=600,
    )

    # Desk impacts often have high acceleration but relatively little rotation.
    # If negative samples exist, raise only those bounds that still retain at
    # least 80% of the user's 15 positive examples.
    if negative_items:
        minimum_kept = math.ceil(len(positive_items) * 0.80)
        dimensions = {
            "confirm_gyro_dps": sorted(
                {profile.confirm_gyro_dps}
                | {round(item.peak_gyro_dps, 1) for item in positive_items + negative_items}
            ),
            "confirm_dynamic_accel_g": sorted(
                {profile.confirm_dynamic_accel_g}
                | {
                    round(item.peak_dynamic_accel_g, 3)
                    for item in positive_items + negative_items
                }
            ),
            "minimum_angular_travel_deg": sorted(
                {profile.minimum_angular_travel_deg}
                | {
                    round(item.angular_travel_deg, 1)
                    for item in positive_items + negative_items
                }
            ),
            "minimum_dominant_axis_ratio": sorted(
                {profile.minimum_dominant_axis_ratio}
                | {
                    round(item.dominant_axis_ratio, 3)
                    for item in positive_items + negative_items
                }
            ),
            "maximum_peak_gap_ms": sorted(
                {profile.maximum_peak_gap_ms}
                | {item.peak_gap_ms for item in positive_items + negative_items}
            ),
        }

        def rank(candidate: DetectorProfile) -> tuple[int, int, float]:
            kept, false_positives = profile_score(
                candidate, positive_items, negative_items
            )
            if kept < minimum_kept:
                return (-10000 + kept, -false_positives, 0.0)
            # False positives cost more than losing a small amount of recall.
            return (
                kept * 10 - false_positives * 28,
                -false_positives,
                kept / len(positive_items),
            )

        for _ in range(3):
            for name, candidates in dimensions.items():
                best = profile
                best_rank = rank(profile)
                for value in candidates:
                    candidate = replace(profile, **{name: value})
                    try:
                        candidate.validated()
                    except ValueError:
                        continue
                    candidate_rank = rank(candidate)
                    if candidate_rank > best_rank:
                        best = candidate
                        best_rank = candidate_rank
                profile = best

        max_negative_accel = max(
            item.peak_dynamic_accel_g for item in negative_items
        )
        positive_high_accel = _percentile(
            (item.peak_dynamic_accel_g for item in positive_items), 0.90
        )
        if max_negative_accel >= profile.hard_dynamic_accel_g:
            replacement = max_negative_accel * 1.08
            if replacement <= positive_high_accel * 1.12:
                profile = replace(
                    profile, hard_dynamic_accel_g=round(replacement, 3)
                )
            else:
                profile = replace(profile, hard_dynamic_accel_g=12.0)

    return profile.validated()


def sample_averages(samples: Iterable[LearningSample]) -> dict[str, float]:
    items = tuple(samples)
    if not items:
        return {}
    names = (
        "peak_gyro_dps",
        "peak_dynamic_accel_g",
        "duration_ms",
        "angular_travel_deg",
        "dominant_axis_ratio",
        "peak_gap_ms",
    )
    return {
        name: sum(float(getattr(item, name)) for item in items) / len(items)
        for name in names
    }
