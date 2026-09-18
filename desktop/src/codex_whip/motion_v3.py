from __future__ import annotations

import json
import math
import statistics
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from .models import RawMotionBatch, RawMotionFrame, WhipEvent
from .paths import user_data_dir


FEATURE_POINTS = 48
RING_DURATION_MS = 1800


@dataclass(frozen=True, slots=True)
class MotionTemplate:
    label: str
    features: tuple[tuple[float, float, float], ...]
    peak_gyro_dps: float
    peak_dynamic_accel_g: float
    duration_ms: int


@dataclass(frozen=True, slots=True)
class MotionProfile:
    positive_templates: tuple[MotionTemplate, ...]
    negative_templates: tuple[MotionTemplate, ...]
    acceptance_distance: float
    negative_margin: float
    tolerance: float = 1.0

    @property
    def trained(self) -> bool:
        return bool(self.positive_templates)


def default_motion_profile_path() -> Path:
    return user_data_dir() / "motion-profile-v3.json"


def _magnitudes(frame: RawMotionFrame) -> tuple[float, float]:
    gyro = math.sqrt(
        frame.gyro_x_dps**2 + frame.gyro_y_dps**2 + frame.gyro_z_dps**2
    )
    accel = math.sqrt(frame.accel_x_g**2 + frame.accel_y_g**2 + frame.accel_z_g**2)
    return gyro, abs(accel - 1.0)


def _feature_rows(frames: Sequence[RawMotionFrame]) -> list[tuple[int, float, float, float]]:
    rows: list[tuple[int, float, float, float]] = []
    previous_dynamic: float | None = None
    previous_time: int | None = None
    for frame in frames:
        gyro, dynamic = _magnitudes(frame)
        jerk = 0.0
        if previous_dynamic is not None and previous_time is not None:
            elapsed = max(0.001, (frame.timestamp_ms - previous_time) / 1000.0)
            jerk = abs(dynamic - previous_dynamic) / elapsed
        rows.append(
            (
                frame.timestamp_ms,
                min(3.0, gyro / 1200.0),
                min(3.0, dynamic / 2.0),
                min(3.0, jerk / 35.0),
            )
        )
        previous_dynamic = dynamic
        previous_time = frame.timestamp_ms
    return rows


def _resample(
    rows: Sequence[tuple[int, float, float, float]], points: int = FEATURE_POINTS
) -> tuple[tuple[float, float, float], ...]:
    if len(rows) < 2:
        raise ValueError("动作数据不足")
    start, end = rows[0][0], rows[-1][0]
    if end <= start:
        raise ValueError("动作时间序列无效")
    result: list[tuple[float, float, float]] = []
    cursor = 0
    for index in range(points):
        target = start + (end - start) * index / (points - 1)
        while cursor + 1 < len(rows) and rows[cursor + 1][0] < target:
            cursor += 1
        left = rows[cursor]
        right = rows[min(cursor + 1, len(rows) - 1)]
        span = right[0] - left[0]
        amount = 0.0 if span <= 0 else (target - left[0]) / span
        result.append(
            tuple(
                left[channel] + (right[channel] - left[channel]) * amount
                for channel in range(1, 4)
            )
        )
    return tuple(result)


def build_motion_features(
    frames: Sequence[RawMotionFrame], points: int = FEATURE_POINTS
) -> tuple[tuple[float, float, float], ...]:
    """Build the same gyro/acceleration/jerk trajectory used by V3 matching."""
    return _resample(_feature_rows(frames), points)


def extract_template(
    frames: Sequence[RawMotionFrame], label: str
) -> MotionTemplate:
    if len(frames) < 24:
        raise ValueError("还没收到完整动作，请挥动后立即录入")
    metrics = [_magnitudes(frame) for frame in frames]
    peak_index = max(range(len(frames)), key=lambda index: metrics[index][0] + 420 * metrics[index][1])
    peak_time = frames[peak_index].timestamp_ms
    window_start = peak_time - 440
    window_end = peak_time + 280
    selected = [
        frame for frame in frames if window_start <= frame.timestamp_ms <= window_end
    ]
    if len(selected) < 24:
        raise ValueError("动作前后数据不足，请等待连接稳定后重试")
    peak_gyro = max(_magnitudes(frame)[0] for frame in selected)
    peak_dynamic = max(_magnitudes(frame)[1] for frame in selected)
    if peak_gyro < 100.0:
        raise ValueError("最近动作过弱，未找到明显挥鞭轨迹")
    return MotionTemplate(
        label=label,
        features=build_motion_features(selected),
        peak_gyro_dps=peak_gyro,
        peak_dynamic_accel_g=peak_dynamic,
        duration_ms=selected[-1].timestamp_ms - selected[0].timestamp_ms,
    )


def dtw_distance(
    first: Sequence[tuple[float, float, float]],
    second: Sequence[tuple[float, float, float]],
) -> float:
    previous = [math.inf] * (len(second) + 1)
    previous[0] = 0.0
    for left in first:
        current = [math.inf] * (len(second) + 1)
        for column, right in enumerate(second, start=1):
            cost = math.sqrt(
                (left[0] - right[0]) ** 2
                + 1.20 * (left[1] - right[1]) ** 2
                + 0.65 * (left[2] - right[2]) ** 2
            )
            current[column] = cost + min(
                current[column - 1], previous[column], previous[column - 1]
            )
        previous = current
    return previous[-1] / max(1, len(first) + len(second))


def _nearest(template: MotionTemplate, others: Sequence[MotionTemplate]) -> float:
    return min(dtw_distance(template.features, other.features) for other in others)


def _representatives(
    templates: Sequence[MotionTemplate], maximum: int = 8
) -> tuple[MotionTemplate, ...]:
    if len(templates) <= maximum:
        return tuple(templates)
    distances = [
        sum(
            dtw_distance(item.features, other.features)
            for other_index, other in enumerate(templates)
            if other_index != item_index
        )
        for item_index, item in enumerate(templates)
    ]
    selected = [min(range(len(templates)), key=distances.__getitem__)]
    while len(selected) < maximum:
        remaining = [index for index in range(len(templates)) if index not in selected]
        selected.append(
            max(
                remaining,
                key=lambda index: min(
                    dtw_distance(templates[index].features, templates[chosen].features)
                    for chosen in selected
                ),
            )
        )
    return tuple(templates[index] for index in selected)


def train_motion_profile(
    positives: Sequence[MotionTemplate], negatives: Sequence[MotionTemplate] = ()
) -> MotionProfile:
    if len(positives) < 3:
        raise ValueError("至少需要 3 次挥鞭样本")
    within = [
        _nearest(
            item,
            [other for other_index, other in enumerate(positives) if other_index != item_index],
        )
        for item_index, item in enumerate(positives)
    ]
    ordered = sorted(within)
    percentile = ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.9))]
    acceptance = max(0.035, percentile * 1.28)
    margin = 0.01
    if negatives:
        negative_distances = [
            min(dtw_distance(item.features, positive.features) for positive in positives)
            for item in negatives
        ]
        nearest_negative = min(negative_distances)
        acceptance = min(acceptance, max(0.025, nearest_negative * 0.88))
        margin = max(0.008, min(0.08, nearest_negative * 0.12))
    return MotionProfile(
        positive_templates=_representatives(positives),
        negative_templates=_representatives(negatives, maximum=5),
        acceptance_distance=acceptance,
        negative_margin=margin,
    )


def _template_to_dict(template: MotionTemplate) -> dict[str, object]:
    return {
        "label": template.label,
        "features": [list(row) for row in template.features],
        "peak_gyro_dps": template.peak_gyro_dps,
        "peak_dynamic_accel_g": template.peak_dynamic_accel_g,
        "duration_ms": template.duration_ms,
    }


def save_motion_profile(profile: MotionProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "schema_version": 3,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "acceptance_distance": profile.acceptance_distance,
        "negative_margin": profile.negative_margin,
        "tolerance": profile.tolerance,
        "positive_templates": [_template_to_dict(item) for item in profile.positive_templates],
        "negative_templates": [_template_to_dict(item) for item in profile.negative_templates],
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_motion_profile(path: Path) -> MotionProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 3:
            return None

        def restore(item: dict[str, object]) -> MotionTemplate:
            return MotionTemplate(
                label=str(item["label"]),
                features=tuple(tuple(float(value) for value in row) for row in item["features"]),
                peak_gyro_dps=float(item["peak_gyro_dps"]),
                peak_dynamic_accel_g=float(item["peak_dynamic_accel_g"]),
                duration_ms=int(item["duration_ms"]),
            )

        return MotionProfile(
            positive_templates=tuple(restore(item) for item in data["positive_templates"]),
            negative_templates=tuple(restore(item) for item in data.get("negative_templates", [])),
            acceptance_distance=float(data["acceptance_distance"]),
            negative_margin=float(data["negative_margin"]),
            tolerance=min(1.8, max(0.6, float(data.get("tolerance", 1.0)))),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


class MotionEngine:
    """Thread-safe raw motion buffer, recorder and personalized classifier."""

    def __init__(self, profile_path: Path | None = None) -> None:
        self.profile_path = profile_path or default_motion_profile_path()
        self.profile = load_motion_profile(self.profile_path)
        self._frames: deque[RawMotionFrame] = deque()
        self._lock = threading.Lock()
        self._active_since: int | None = None
        self._peak_time: int | None = None
        self._quiet_since: int | None = None
        self._last_trigger_ms = -10_000
        self._sequence = 2_000_000
        self.last_score: tuple[float, float | None, bool] | None = None
        self._paused = False
        self._last_frame_ms: int | None = None
        self.last_rejection: str | None = None
        self._await_quiet = False

    @property
    def trained(self) -> bool:
        return bool(self.profile and self.profile.trained)

    @property
    def tolerance_percent(self) -> int:
        return round((self.profile.tolerance if self.profile else 1.0) * 100)

    def capture_recent(self, label: str) -> MotionTemplate:
        with self._lock:
            frames = tuple(self._frames)
        return extract_template(frames, label)

    def set_paused(self, paused: bool) -> None:
        with self._lock:
            self._paused = bool(paused)
            if paused:
                self._active_since = None
                self._peak_time = None
                self._quiet_since = None
                self._await_quiet = False

    def train(
        self, positives: Sequence[MotionTemplate], negatives: Sequence[MotionTemplate]
    ) -> MotionProfile:
        profile = train_motion_profile(positives, negatives)
        save_motion_profile(profile, self.profile_path)
        with self._lock:
            self.profile = profile
        return profile

    def set_tolerance_percent(self, percent: int) -> None:
        value = min(180, max(60, int(percent))) / 100.0
        with self._lock:
            if self.profile is None:
                return
            self.profile = MotionProfile(
                self.profile.positive_templates,
                self.profile.negative_templates,
                self.profile.acceptance_distance,
                self.profile.negative_margin,
                value,
            )
            profile = self.profile
        save_motion_profile(profile, self.profile_path)

    def clear_profile(self) -> None:
        with self._lock:
            self.profile = None
            self.last_score = None
        try:
            self.profile_path.unlink()
        except FileNotFoundError:
            pass

    def classify(self, template: MotionTemplate) -> bool:
        profile = self.profile
        if profile is None or not profile.trained:
            return False
        positive = min(
            dtw_distance(template.features, item.features)
            for item in profile.positive_templates
        )
        negative = (
            min(
                dtw_distance(template.features, item.features)
                for item in profile.negative_templates
            )
            if profile.negative_templates
            else None
        )
        accepted = positive <= profile.acceptance_distance * profile.tolerance
        if negative is not None:
            accepted = accepted and positive + profile.negative_margin < negative
        self.last_score = (positive, negative, accepted)
        return accepted

    def feed_batch(self, batch: RawMotionBatch) -> WhipEvent | None:
        detected: WhipEvent | None = None
        with self._lock:
            for frame in batch.frames:
                if self._last_frame_ms is not None:
                    gap = frame.timestamp_ms - self._last_frame_ms
                    if gap == 0 or -1000 < gap < 0:
                        continue
                    if gap < 0 or gap > 250:
                        self._frames.clear()
                        self._active_since = self._peak_time = self._quiet_since = None
                        self._await_quiet = False
                        self._last_trigger_ms = frame.timestamp_ms - 10000
                self._last_frame_ms = frame.timestamp_ms
                self._frames.append(frame)
                cutoff = frame.timestamp_ms - RING_DURATION_MS
                while self._frames and self._frames[0].timestamp_ms < cutoff:
                    self._frames.popleft()
                if (
                    not self._paused
                    and self.profile is not None
                    and self.profile.trained
                ):
                    event = self._feed_frame_locked(frame)
                    if event is not None:
                        detected = event
        return detected

    def _feed_frame_locked(self, frame: RawMotionFrame) -> WhipEvent | None:
        gyro, dynamic = _magnitudes(frame)
        median_peak = statistics.median(
            item.peak_gyro_dps for item in self.profile.positive_templates
        )
        start_gyro = min(360.0, max(110.0, median_peak * 0.18))
        now = frame.timestamp_ms
        if self._await_quiet:
            if gyro < start_gyro * 0.55 and dynamic < 0.10:
                if self._quiet_since is None:
                    self._quiet_since = now
                elif now - self._quiet_since >= 90:
                    self._await_quiet = False
                    self._quiet_since = None
                    self._last_trigger_ms = now
            else:
                self._quiet_since = None
            return None
        if self._active_since is None:
            if now - self._last_trigger_ms >= 400 and gyro >= start_gyro and (
                dynamic >= 0.08 or gyro >= start_gyro * 1.55
            ):
                self._active_since = now
                self._peak_time = now
                self._quiet_since = None
            return None

        if self._peak_time is None:
            self._peak_time = now
        peak_frame = min(self._frames, key=lambda item: abs(item.timestamp_ms - self._peak_time))
        peak_energy = _magnitudes(peak_frame)[0] + 420 * _magnitudes(peak_frame)[1]
        if gyro + 420 * dynamic > peak_energy:
            self._peak_time = now

        if gyro < start_gyro * 0.55 and dynamic < 0.10:
            self._quiet_since = self._quiet_since or now
        else:
            self._quiet_since = None

        enough_tail = now - self._peak_time >= 280
        quiet = self._quiet_since is not None and now - self._quiet_since >= 90
        timed_out = now - self._active_since >= 1250
        if not ((enough_tail and quiet) or timed_out):
            return None

        active_since = self._active_since
        self._active_since = None
        self._peak_time = None
        self._quiet_since = None
        # A template match must never turn an endless shake into a whip simply
        # because the candidate timed out. Require a genuine braking/quiet tail.
        if not quiet:
            self.last_rejection = "no_braking_tail"
            self._last_trigger_ms = now
            self._await_quiet = True
            return None
        selected = [item for item in self._frames if active_since <= item.timestamp_ms <= now]
        travel = 0.0
        fast_frames = 0
        for previous, item in zip(selected, selected[1:]):
            dt = (item.timestamp_ms - previous.timestamp_ms) / 1000.0
            travel += (_magnitudes(previous)[0] + _magnitudes(item)[0]) * 0.5 * dt
            fast_frames += _magnitudes(item)[0] >= start_gyro
        template_travel = statistics.median(
            sum(row[0] * 1200.0 for row in item.features)
            * item.duration_ms / 1000.0 / len(item.features)
            for item in self.profile.positive_templates
        )
        minimum_travel = max(8.0, min(25.0, template_travel * 0.22 / self.profile.tolerance))
        if travel < minimum_travel or fast_frames < 3:
            self.last_rejection = "insufficient_rotation_process"
            return None
        try:
            # Do not re-use the strongest (already consumed) whip in the ring.
            candidate = extract_template(
                tuple(item for item in self._frames if item.timestamp_ms >= active_since - 160),
                "candidate",
            )
        except ValueError:
            return None
        if not self.classify(candidate):
            self.last_rejection = "template_mismatch"
            return None

        self.last_rejection = None
        self._sequence += 1
        self._last_trigger_ms = now
        selected = [item for item in self._frames if active_since <= item.timestamp_ms <= now]
        peak_accel = max(
            math.sqrt(item.accel_x_g**2 + item.accel_y_g**2 + item.accel_z_g**2)
            for item in selected
        )
        angular_travel = 0.0
        peak_jerk = 0.0
        previous: RawMotionFrame | None = None
        previous_dynamic = 0.0
        for item in selected:
            item_gyro, item_dynamic = _magnitudes(item)
            if previous is not None:
                seconds = max(0.001, (item.timestamp_ms - previous.timestamp_ms) / 1000.0)
                angular_travel += item_gyro * seconds
                peak_jerk = max(peak_jerk, abs(item_dynamic - previous_dynamic) / seconds)
            previous = item
            previous_dynamic = item_dynamic
        return WhipEvent(
            sequence=self._sequence,
            peak_gyro_dps=candidate.peak_gyro_dps,
            peak_accel_g=peak_accel,
            duration_ms=now - active_since,
            angular_travel_deg=angular_travel,
            peak_jerk_gps=peak_jerk,
        )
