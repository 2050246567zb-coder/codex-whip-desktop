"""Guided, threshold-independent direction learning from raw IMU motion."""
from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict

from .models import RawMotionBatch
from .mount_profile import MountingProfile
from .sensor_pose import (
    SensorPoseTracker, _conjugate, _continuous_rotation_vector, _cross, _dot,
    _multiply, _unit,
)


def length(v) -> float:
    return math.sqrt(_dot(v, v))


def rotation(q):
    if q[0] < 0:
        q = tuple(-v for v in q)
    return _continuous_rotation_vector(q, (0.0, 0.0, 0.0))


class DirectionCalibration:
    """Worker-thread state machine; saving/applying remains an explicit action."""

    def __init__(self, tracker: SensorPoseTracker, *, first: str = "right") -> None:
        if first not in {"right", "up"}:
            raise ValueError("校准方向必须是 right 或 up")
        self.tracker = tracker
        self.first = first
        self.stage = "neutral"
        self.candidate: MountingProfile | None = None
        self.centered = False
        self._last_ms: int | None = None
        self._capture_orientation = None
        self._start_up = (0.0, 0.0, 1.0)
        self._duration = 0
        self._bad_motion = False
        self._shock_ms = self._fast_ms = 0
        self._peak_accel = self._peak_speed = 0.0
        self.right_angle = 0.0
        self.up_angle = 0.0
        self._up_axis = None
        self._yaw_sign = 1
        self.feedback = ""
        self._diagnostic_frames = deque(maxlen=6000)

    def invalidate(self) -> None:
        self.__init__(self.tracker, first=self.first)

    def connection_lost(self) -> None:
        # Installation axes already learned remain valid after a transport
        # interruption. Only the in-flight relative rotation is unknowable.
        self.retry()
        self._last_ms = None
        self.centered = False

    def feed(self, batch: RawMotionBatch) -> str | None:
        for f in batch.frames:
            self._diagnostic_frames.append(f)
            values = (f.gyro_x_dps, f.gyro_y_dps, f.gyro_z_dps,
                      f.accel_x_g, f.accel_y_g, f.accel_z_g)
            delta = 0 if self._last_ms is None else (f.timestamp_ms - self._last_ms) & 0xFFFFFFFF
            if delta == 0 and self._last_ms is not None:
                continue
            if self._last_ms is not None and -1000 < f.timestamp_ms - self._last_ms < 0:
                continue
            if not all(math.isfinite(v) for v in values) or delta > 120:
                self.connection_lost()
                self._last_ms = f.timestamp_ms
                return "六轴数据中断或异常；已完成步骤保留。连接恢复后重录当前动作；预览页需重新归中。"
            self._last_ms = f.timestamp_ms
            if self.stage.endswith("_capture"):
                speed = length(tuple(values[i] - self.tracker.gyro_bias[i] for i in range(3)))
                self._duration += delta
                accel = length(values[3:])
                self._peak_accel = max(self._peak_accel, accel)
                self._peak_speed = max(self._peak_speed, speed)
                # Translation accompanies a normal wrist arc. It must not
                # invalidate a gyro-based direction just because |a| != 1g.
                self._shock_ms = self._shock_ms + delta if accel > 3.0 or accel < .2 else 0
                self._fast_ms = self._fast_ms + delta if speed > 600 else 0
                self._bad_motion |= (accel > 6.0 or self._shock_ms >= 40 or self._fast_ms >= 40
                                     or max(abs(v) for v in values[:3]) >= 1900)
                # No wall-clock deadline and no growing trajectory list. A
                # pause while reaching for the mouse never erases this step.
        return None

    def diagnostic_snapshot(self) -> dict:
        """Bounded sensor-only trace; no microphone, message or desktop data."""
        return {
            "stage": self.stage, "capture_ms": self._duration, "angle_deg": self.angle,
            "peak_gyro_dps": self._peak_speed, "peak_accel_g": self._peak_accel,
            "bad_motion": self._bad_motion, "gyro_bias": self.tracker.gyro_bias,
            "start_orientation": self._capture_orientation,
            "end_orientation": self.tracker.orientation, "start_up": self._start_up,
            "yaw_sign": self._yaw_sign,
            "candidate": asdict(self.candidate) if self.candidate else None,
            "frames": [asdict(f) for f in self._diagnostic_frames
                       if all(math.isfinite(v) for v in asdict(f).values())],
        }

    def record_neutral(self) -> None:
        reading = self.tracker.orientation_reading()
        if not reading.ready:
            raise ValueError(reading.detail)
        # Initial setup cannot depend on a potentially wrong previous mount.
        old = self.tracker.mounting
        self.tracker.mounting = None
        try:
            self.tracker.calibrate_neutral(allow_motion=True)
        finally:
            self.tracker.mounting = old
        self.stage = f"{self.first}_ready"
        self.candidate = None
        self.centered = False

    def begin(self) -> None:
        if self.stage not in ("right_ready", "up_ready"):
            raise ValueError("请先记录起点。")
        reading = self.tracker.orientation_reading()
        if not reading.ready:
            raise ValueError(reading.detail)
        # Each gesture has a fresh reference. Returning within 10 degrees of
        # the first pose is neither necessary nor observable user intent.
        self._capture_orientation = self.tracker.orientation
        self._start_up = _unit(reading.mean_accel)
        self._duration = 0
        self._bad_motion = False
        self._shock_ms = self._fast_ms = 0
        self._peak_accel = self._peak_speed = 0.0
        self.stage = self.stage.replace("ready", "capture")
        self.feedback = "正在采集，没有时间限制。转到目标后点击录入即可，无需等到完全静止。"

    @property
    def angle(self) -> float:
        if self._capture_orientation is None:
            return 0.0
        return math.degrees(length(rotation(_multiply(_conjugate(self._capture_orientation),
                                                       self.tracker.orientation))))

    def finish(self) -> None:
        if self.stage not in ("right_capture", "up_capture"):
            raise ValueError("请先点击开始采集。")
        reading = self.tracker.orientation_reading()
        if not reading.ready:
            raise ValueError(reading.detail)
        if not 10 <= self.angle <= 100:
            raise ValueError(f"本次净转角 {self.angle:.0f}°，暂不足以学习或转角过大。"
                             "采集仍保留：把手柄转到离开始位置约 20–40° 后再点录入，无需重开。")
        if self._bad_motion:
            raise ValueError(f"检测到强冲击或急甩（{self._peak_accel:.1f}g / {self._peak_speed:.0f}°/s）；"
                             "请重录本步，正常转腕即可，无需用力挥鞭。")
        rv = rotation(_multiply(_conjugate(self._capture_orientation), self.tracker.orientation))
        total = length(rv)
        yaw = _dot(rv, self._start_up)
        if self.stage == "right_capture":
            if abs(math.degrees(yaw)) < 8:
                raise ValueError(f"左右转动分量仅 {abs(math.degrees(yaw)):.0f}°，尚不足以学习。"
                                 "当前采集保留，请向右再转一点后点录入；不必重开。")
            # The user's demonstration defines screen-right. Never assume a
            # positive gyro/gravity dot product is a physical right turn.
            self._yaw_sign = 1 if yaw > 0 else -1
            self.right_angle = self.angle
            if self.first == "up":
                self.candidate = MountingProfile(
                    _unit(_cross(self._start_up, self._up_axis)), self.right_angle,
                    self.up_angle, schema_version=2, yaw_sign=self._yaw_sign,
                ).validated()
                self.stage = "review"
                self.feedback = "两个方向已录入。请归中试转，确认跟随方向正确后保存。"
            else:
                self.stage = "up_ready"
                self.feedback = "已学到向右的方向。恢复舒服的握姿即可开始向上采集，不必精确返回起点。"
        else:
            horizontal = tuple(rv[i] - yaw * self._start_up[i] for i in range(3))
            if math.degrees(length(horizontal)) < 8 or length(horizontal) / total < .30:
                raise ValueError("本次动作与左右转动太接近，上抬分量不足。"
                                 "当前采集保留：向上再抬一点后点录入，不必重开。")
            axis = _unit(horizontal)
            self._up_axis = axis
            self.up_angle = self.angle
            if self.first == "up":
                self.stage = "right_ready"
                self.feedback = "已学到向上的方向。恢复舒服的握姿，接下来向右转动。"
            else:
                self.candidate = MountingProfile(
                    _unit(_cross(self._start_up, axis)), self.right_angle, self.up_angle,
                    schema_version=2, yaw_sign=self._yaw_sign,
                ).validated()
                self.stage = "review"
                self.feedback = "两个方向已录入。请归中试转；只有确认跟随方向正确后才会保存。"

    def retry(self) -> None:
        if self.stage in ("right_capture", "up_capture"):
            self.stage = self.stage.replace("capture", "ready")
            self._capture_orientation = None
            self.feedback = "仅重录当前动作，前面已完成的步骤保留。"
