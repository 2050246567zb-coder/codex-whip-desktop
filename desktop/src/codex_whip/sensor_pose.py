from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass

from .models import RawMotionBatch, RawMotionFrame
from .mount_profile import MountingProfile

Vector = tuple[float, float, float]
Quaternion = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class SensorPose:
    """Relative on-screen pose; deliberately not an absolute 3D position."""

    offset_x: float
    offset_y: float
    angle_degrees: float
    activity: float
    moving: bool = False
    auto_centered: bool = False
    bias_trimmed: bool = False


@dataclass(frozen=True, slots=True)
class GripStability:
    ready: bool
    detail: str
    mean_gyro: Vector = (0.0, 0.0, 0.0)
    mean_accel: Vector = (0.0, 0.0, 0.0)
    bias_quiet: bool = False


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(maximum, max(minimum, value))


def _dot(a: Vector, b: Vector) -> float:
    return sum(x * y for x, y in zip(a, b))


def _cross(a: Vector, b: Vector) -> Vector:
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def _unit(v: Vector) -> Vector:
    norm = math.sqrt(_dot(v, v))
    return tuple(x / norm for x in v) if norm > 1e-9 else (0.0, 0.0, 1.0)


def _multiply(a: Quaternion, b: Quaternion) -> Quaternion:
    w, x, y, z = a
    v, i, j, k = b
    return (w*v-x*i-y*j-z*k, w*i+x*v+y*k-z*j,
            w*j-x*k+y*v+z*i, w*k+x*j-y*i+z*v)


def _conjugate(q: Quaternion) -> Quaternion:
    return (q[0], -q[1], -q[2], -q[3])


def _normalize(q: Quaternion) -> Quaternion:
    norm = math.sqrt(sum(value * value for value in q))
    return tuple(value / norm for value in q)


def _rotate(q: Quaternion, v: Vector) -> Vector:
    return _multiply(_multiply(q, (0.0, *v)), _conjugate(q))[1:]


def _gravity_orientation(gravity: Vector) -> Quaternion:
    """Shortest rotation from measured body gravity to world +Z."""
    gx, gy, gz = _unit(gravity)
    if gz < -0.99999:
        return (0.0, 1.0, 0.0, 0.0)
    return _normalize((1.0 + gz, gy, -gx, 0.0))


def _continuous_rotation_vector(q: Quaternion, previous: Vector) -> Vector:
    """Quaternion logarithm, choosing the equivalent turn nearest last frame.

    Ray/Euler projections become singular at 90 degrees and wrap at 180.
    A relative rotation vector avoids those poles without integrating position.
    """
    size = math.sqrt(sum(v*v for v in q[1:]))
    if size < 1e-8:
        length = math.sqrt(_dot(previous, previous))
        turns = round(length / math.tau)
        return tuple(v * turns * math.tau / length for v in previous) if length > 1e-8 else (0.0, 0.0, 0.0)
    axis = tuple(v / size for v in q[1:])
    angle = 2.0 * math.atan2(size, q[0])
    turns = round((_dot(previous, axis) - angle) / math.tau)
    return tuple(v * (angle + turns * math.tau) for v in axis)


class SensorPoseTracker:
    """Quaternion attitude -> calibrated pointing, with no translation integrator.

    Calibration defines screen-up from gravity and screen-right from the learned
    pointing axis. Unconfigured boards retain the legacy PCB-axis fallback.
    A six-axis IMU has no absolute yaw reference; neutral is always relative.
    """

    MAX_OFFSET_X_PX = 190.0
    MAX_OFFSET_Y_PX = 130.0
    MAX_ANGLE_DEGREES = 42.0
    HORIZONTAL_RANGE_DEGREES = 25.0
    VERTICAL_RANGE_DEGREES = 20.0
    GYRO_DEADBAND_DPS = 0.25
    MOTION_GYRO_THRESHOLD_DPS = 1.0
    MAX_SAMPLE_GAP_MS = 250
    CALIBRATION_WINDOW_MS = 500
    # Hand-held stability is not laboratory stillness: bounded oscillation is
    # allowed, but sustained rotation and impacts are not calibration poses.
    HOLD_GYRO_RMS_DPS = 12.0
    HOLD_GYRO_PEAK_DPS = 40.0
    HOLD_NET_RATE_DPS = 2.5
    HOLD_ANGLE_SPAN_DEG = 2.0
    HOLD_ACCEL_RMS_G = 0.065
    GRAVITY_CORRECTION_GAIN = 1.2
    AUTO_CENTER_DELAY_MS = 3000
    # Deliberately generous for a hand-held product; bounded tremor is quiet.
    IDLE_GYRO_DPS = 12.0
    IDLE_ACCEL_DEVIATION_G = 0.20
    IDLE_MAX_SWAY_DEGREES = 3.0
    IDLE_MAX_SAMPLE_GAP_MS = 120
    # A new board, or an IMU that has just resumed from deep sleep, can retain
    # a small constant gyro offset even after the firmware's boot calibration.
    # The desktop is allowed to learn that offset only once after connect/wake,
    # and only from a continuous three-second, low-variance held window.
    BIAS_TRIM_DELAY_MS = 3000
    BIAS_TRIM_MAX_RESIDUAL_DPS = 8.0
    BIAS_TRIM_GYRO_RMS_DPS = 0.9
    BIAS_TRIM_MEAN_CHANGE_DPS = 0.5
    BIAS_TRIM_ACCEL_RMS_G = 0.06

    def __init__(self, mounting: MountingProfile | None = None) -> None:
        self.mounting = mounting.validated() if mounting is not None else None
        self._last_timestamp_ms: int | None = None
        self._q: Quaternion = (1.0, 0.0, 0.0, 0.0)
        self._neutral = self._q
        self._rotation_vector: Vector = (0.0, 0.0, 0.0)
        self._display_zero: Vector = (0.0, 0.0, 0.0)
        self._up: Vector = (0.0, 0.0, 1.0)
        self._right: Vector = (1.0, 0.0, 0.0)
        self._forward: Vector = (0.0, -1.0, 0.0)
        self._bias: Vector = (0.0, 0.0, 0.0)
        self._recent: deque[RawMotionFrame] = deque(maxlen=128)
        self._gyro_filter: deque[Vector] = deque(maxlen=3)
        self._pose = SensorPose(0.0, 0.0, 0.0, 0.0)
        self._idle_anchor: Quaternion | None = None
        self._idle_ms = 0
        self._batch_auto_centered = False
        self._batch_bias_trimmed = False
        self._bias_trim_requested = False
        self._bias_trim_anchor: Vector | None = None
        self._bias_trim_ms = 0

    def reset(self) -> None:
        bias = self._bias
        trim_requested = self._bias_trim_requested
        self.__init__(self.mounting)
        self._bias = bias
        self._bias_trim_requested = trim_requested

    def set_device_bias(self, bias: Vector) -> None:
        """Install an explicitly measured bias and discard old-device attitude."""
        if len(bias) != 3 or not all(math.isfinite(v) for v in bias) or math.sqrt(_dot(bias, bias)) > 15:
            raise ValueError("Invalid stationary gyro bias")
        self.__init__(self.mounting)
        self._bias = tuple(float(v) for v in bias)

    def request_idle_bias_trim(self) -> None:
        """Arm one session-only zero-rate correction after connect or wake.

        A six-axis IMU cannot distinguish constant yaw from gyro bias while it
        is moving.  The product gesture resolves that ambiguity: point the
        handle at the screen and hold it for three seconds.  This flag keeps
        the correction bounded to that explicit connect/wake window instead
        of continuously learning away intentional slow turns.
        """
        self._bias_trim_requested = True
        self._bias_trim_anchor = None
        self._bias_trim_ms = 0

    @property
    def orientation(self) -> Quaternion:
        return self._q

    @property
    def gyro_bias(self) -> Vector:
        return self._bias

    def _mounting_right(self, gravity: Vector) -> Vector | None:
        if self.mounting is None:
            return None
        right = _cross(_unit(gravity), self.mounting.forward)
        if math.sqrt(_dot(right, right)) < 0.35:
            raise ValueError("手柄接近竖直，无法稳定定义左右。请朝向屏幕、稍微放平后再校准。")
        return _unit(right)

    def _set_neutral(self, gravity: Vector) -> None:
        self._reset_idle()
        self._neutral = self._q
        self._rotation_vector = (0.0, 0.0, 0.0)
        self._display_zero = (0.0, 0.0, 0.0)
        self._up = _unit(gravity)
        candidate = (1.0, 0.0, 0.0) if abs(self._up[0]) < 0.9 else (0.0, 1.0, 0.0)
        projection = _dot(candidate, self._up)
        self._right = self._mounting_right(gravity) or _unit(tuple(candidate[i] - projection * self._up[i] for i in range(3)))
        self._forward = _cross(self._right, self._up)
        self._pose = SensorPose(0.0, 0.0, 0.0, 0.0)

    def _reset_idle(self) -> None:
        self._idle_anchor = None
        self._idle_ms = 0

    def _reset_bias_trim_window(self) -> None:
        self._bias_trim_anchor = None
        self._bias_trim_ms = 0

    def _trim_requested_bias(self, delta: int) -> bool:
        """Remove a stable residual rate once after connect/wake.

        This deliberately uses raw gyro means because ``_bias`` is the
        desktop-side correction.  It is not persisted: the firmware performs
        a fresh calibration on each power cycle, so carrying the residual into
        a later boot could double-correct a different hardware state.
        """
        if not self._bias_trim_requested:
            return False
        frames = tuple(self._recent)
        elapsed = ((frames[-1].timestamp_ms - frames[0].timestamp_ms) & 0xFFFFFFFF) if len(frames) >= 2 else 0
        if (delta <= 0 or delta > self.IDLE_MAX_SAMPLE_GAP_MS
                or len(frames) < 10 or elapsed < 400):
            self._reset_bias_trim_window()
            return False
        gyros = [(f.gyro_x_dps, f.gyro_y_dps, f.gyro_z_dps) for f in frames]
        accels = [(f.accel_x_g, f.accel_y_g, f.accel_z_g) for f in frames]
        mean_gyro = tuple(sum(v[i] for v in gyros) / len(gyros) for i in range(3))
        mean_accel = tuple(sum(v[i] for v in accels) / len(accels) for i in range(3))
        residual = tuple(mean_gyro[i] - self._bias[i] for i in range(3))
        residual_speed = math.sqrt(_dot(residual, residual))
        gyro_rms = math.sqrt(sum(math.dist(v, mean_gyro) ** 2 for v in gyros) / len(gyros))
        accel_rms = math.sqrt(sum(math.dist(v, mean_accel) ** 2 for v in accels) / len(accels))
        accel_norm = math.sqrt(_dot(mean_accel, mean_accel))
        if (residual_speed > self.BIAS_TRIM_MAX_RESIDUAL_DPS
                or gyro_rms > self.BIAS_TRIM_GYRO_RMS_DPS
                or accel_rms > self.BIAS_TRIM_ACCEL_RMS_G
                or not 0.88 <= accel_norm <= 1.12):
            self._reset_bias_trim_window()
            return False
        if (self._bias_trim_anchor is None
                or math.dist(residual, self._bias_trim_anchor) > self.BIAS_TRIM_MEAN_CHANGE_DPS):
            self._bias_trim_anchor = residual
            self._bias_trim_ms = 0
            return False
        self._bias_trim_anchor = tuple(
            self._bias_trim_anchor[i] * 0.9 + residual[i] * 0.1 for i in range(3)
        )
        self._bias_trim_ms += delta
        if self._bias_trim_ms < self.BIAS_TRIM_DELAY_MS:
            return False
        self._bias = mean_gyro
        self._bias_trim_requested = False
        self._reset_bias_trim_window()
        self._set_neutral(mean_accel)
        self._batch_auto_centered = True
        self._batch_bias_trimmed = True
        return True

    def _auto_center_if_idle(self, delta: int, speed: float, accel_norm: float) -> None:
        if (delta <= 0 or delta > self.IDLE_MAX_SAMPLE_GAP_MS
                or speed > self.IDLE_GYRO_DPS
                or abs(accel_norm - 1.0) > self.IDLE_ACCEL_DEVIATION_G):
            self._reset_idle()
            return
        if self._idle_anchor is None:
            self._idle_anchor = self._q
            self._idle_ms = 0
            return
        dot = abs(sum(a*b for a, b in zip(self._idle_anchor, self._q)))
        sway = math.degrees(2 * math.acos(_clamp(dot, 0.0, 1.0)))
        if sway > self.IDLE_MAX_SWAY_DEGREES:
            # A small instantaneous rate can still be a deliberate slow turn.
            self._idle_anchor = self._q
            self._idle_ms = 0
            return
        self._idle_ms += delta
        if self._idle_ms < self.AUTO_CENTER_DELAY_MS:
            return
        # Redefine the control frame, not the physical attitude or gyro bias.
        # Average the final quiet window so a single tremor sample cannot tilt
        # the new screen axes. The persisted mounting axis stays in PCB space.
        samples = tuple(self._recent)
        gravity = tuple(sum(getattr(f, name) for f in samples) / len(samples)
                        for name in ('accel_x_g', 'accel_y_g', 'accel_z_g'))
        try:
            self._mounting_right(gravity)  # Validate before mutating the frame.
        except ValueError:
            # No user interruption for a vertical grip: retain the previous
            # axes and only center the display until a usable grip is held.
            self._display_zero = self._pointing_angles()
            self._pose = SensorPose(0.0, 0.0, 0.0, 0.0)
            self._reset_idle()
        else:
            self._set_neutral(gravity)
        self._batch_auto_centered = True

    def _pointing_angles(self) -> Vector:
        horizontal = math.degrees(_dot(self._rotation_vector, self._up))
        if self.mounting is not None:
            horizontal *= self.mounting.yaw_sign
        return (horizontal, -math.degrees(_dot(self._rotation_vector, self._right)),
                math.degrees(_dot(self._rotation_vector, self._forward)))

    def stationary_reading(self) -> tuple[Vector, Vector] | None:
        """Compatibility API: tolerate bounded hand tremor, not just stillness."""
        reading = self.grip_stability()
        return (reading.mean_gyro, reading.mean_accel) if reading.ready else None

    def grip_stability(self) -> GripStability:
        if len(self._recent) < 10 or (
            (self._recent[-1].timestamp_ms - self._recent[0].timestamp_ms) & 0xFFFFFFFF
        ) < 400:
            return GripStability(False, "正在收集约半秒数据；自然握持即可，允许轻微手抖。")
        if any(((b.timestamp_ms - a.timestamp_ms) & 0xFFFFFFFF) > 120
               for a, b in zip(self._recent, tuple(self._recent)[1:])):
            return GripStability(False, "采样有中断，正在等待连续数据；请保持连接。")
        gyros = [(f.gyro_x_dps, f.gyro_y_dps, f.gyro_z_dps) for f in self._recent]
        accels = [(f.accel_x_g, f.accel_y_g, f.accel_z_g) for f in self._recent]
        mean_gyro = tuple(sum(v[i] for v in gyros) / len(gyros) for i in range(3))
        mean_accel = tuple(sum(v[i] for v in accels) / len(accels) for i in range(3))
        corrected = [tuple(v[i] - self._bias[i] for i in range(3)) for v in gyros]
        rms = math.sqrt(sum(_dot(v, v) for v in corrected) / len(corrected))
        peak = max(math.sqrt(_dot(v, v)) for v in corrected)
        displacement = [0.0, 0.0, 0.0]
        low, high = displacement.copy(), displacement.copy()
        frames = tuple(self._recent)
        for n in range(1, len(frames)):
            dt = ((frames[n].timestamp_ms - frames[n-1].timestamp_ms) & 0xFFFFFFFF) / 1000
            for i in range(3):
                displacement[i] += (corrected[n-1][i] + corrected[n][i]) * .5 * dt
                low[i] = min(low[i], displacement[i])
                high[i] = max(high[i], displacement[i])
        elapsed = ((frames[-1].timestamp_ms - frames[0].timestamp_ms) & 0xFFFFFFFF) / 1000
        net_rate = math.sqrt(_dot(displacement, displacement)) / elapsed
        angle_span = math.dist(low, high)
        accel_rms = math.sqrt(sum(math.dist(v, mean_accel)**2 for v in accels) / len(accels))
        if (not .9 <= math.sqrt(_dot(mean_accel, mean_accel)) <= 1.1
                or any(not .75 <= math.sqrt(_dot(v, v)) <= 1.25 for v in accels)
                or max(math.dist(v, mean_accel) for v in accels) > .2
                or accel_rms > self.HOLD_ACCEL_RMS_G):
            return GripStability(False, f"末尾加速度还在变化（波动 {accel_rms:.2f}g）；"
                                 "请在转到的位置稍停后再录入，无需重新开始。")
        if peak > self.HOLD_GYRO_PEAK_DPS or rms > self.HOLD_GYRO_RMS_DPS:
            return GripStability(False, "转动较快；放松手腕、减小动作后再录入，轻微手抖不影响。")
        if net_rate > self.HOLD_NET_RATE_DPS or angle_span > self.HOLD_ANGLE_SPAN_DEG:
            return GripStability(False, "仍在持续转向；转到目标后稍作停留，允许轻微手抖。")
        # Recentring and gyro bias estimation are separate decisions. Do not
        # subtract a hand's real low-speed motion from all future measurements.
        bias_quiet = (max(math.sqrt(_dot(v, v)) for v in gyros) <= 1.0
                      and max(math.dist(v, mean_gyro) for v in gyros) <= .35
                      and max(math.dist(v, mean_accel) for v in accels) <= .025)
        return GripStability(True, f"可以记录 · 已允许轻微手抖（近半秒转角跨度 {angle_span:.1f}°）",
                             mean_gyro, mean_accel, bias_quiet)

    def orientation_reading(self) -> GripStability:
        """A pose snapshot needs valid samples, not a stationary gyro window.

        Used by the manual direction wizard. Never estimates bias from motion.
        The short window checks transport/impact only; gravity comes from the
        fused attitude, not from translation-contaminated acceleration.
        """
        if not self._recent:
            return GripStability(False, "正在收集六轴数据；请保持连接。")
        last = self._recent[-1].timestamp_ms
        frames = [f for f in self._recent if ((last-f.timestamp_ms) & 0xFFFFFFFF) <= 160]
        if len(frames) < 8 or ((last-frames[0].timestamp_ms) & 0xFFFFFFFF) < 100:
            return GripStability(False, "正在收集连续六轴数据；片刻后可记录，无需静止。")
        if any(((b.timestamp_ms-a.timestamp_ms) & 0xFFFFFFFF) > 120
               for a, b in zip(frames, frames[1:])):
            return GripStability(False, "采样有中断；等待连接恢复即可，已完成步骤保留。")
        gyros = [(f.gyro_x_dps, f.gyro_y_dps, f.gyro_z_dps) for f in frames]
        norms = [math.sqrt(f.accel_x_g**2+f.accel_y_g**2+f.accel_z_g**2) for f in frames]
        if max(abs(v) for g in gyros for v in g) >= 1900 or max(norms) > 6:
            return GripStability(False, "刚才有强冲击或传感器接近量程；稍候再点，已完成步骤保留。")
        gravity = _rotate(_conjugate(self._q), (0.0, 0.0, 1.0))
        return GripStability(True, "可以记录 · 不要求静止，轻微手抖和慢速转动均可",
                             tuple(statistics.median(g[i] for g in gyros) for i in range(3)), gravity)

    def calibrate_neutral(self, *, allow_motion: bool = False) -> SensorPose | None:
        """Rebase a manual pose, or use the strict stationary/bias API."""
        if allow_motion:
            snapshot = self.orientation_reading()
            if not snapshot.ready:
                return None
            # Rebase the current pose without learning hand movement as bias.
            stable = self.grip_stability()
            gravity = stable.mean_accel if stable.ready else snapshot.mean_accel
            self._mounting_right(gravity)
            if stable.ready:
                # Restore measured gravity after any missed motion across a
                # short disconnect. This is optional, never a stillness gate.
                self._q = _gravity_orientation(gravity)
            self._set_neutral(gravity)
            return self._pose
        reading = self.grip_stability()
        if not reading.ready:
            return None
        mean_gyro, mean_accel = reading.mean_gyro, reading.mean_accel
        self._mounting_right(mean_accel)  # Reject singular poses before mutating.
        if reading.bias_quiet:
            self._bias = mean_gyro
        self._q = _gravity_orientation(mean_accel)
        self._set_neutral(mean_accel)
        return self._pose

    def feed_batch(self, batch: RawMotionBatch, *, auto_center: bool = False) -> SensorPose:
        """Auto centering is opt-in so calibration/learning can retain its origin."""
        self._batch_auto_centered = False
        self._batch_bias_trimmed = False
        if not auto_center:
            self._reset_idle()
        activity = 0.0
        moving = False
        for frame in batch.frames:
            result = self._feed_frame(frame, auto_center=auto_center)
            if result is not None:
                frame_activity, frame_moving = result
                activity = max(activity, frame_activity)
                moving = moving or frame_moving
        return SensorPose(self._pose.offset_x, self._pose.offset_y,
                          self._pose.angle_degrees, activity, moving,
                          self._batch_auto_centered, self._batch_bias_trimmed)

    def _feed_frame(self, frame: RawMotionFrame, *, auto_center: bool = False) -> tuple[float, bool] | None:
        gyro = (frame.gyro_x_dps, frame.gyro_y_dps, frame.gyro_z_dps)
        accel = (frame.accel_x_g, frame.accel_y_g, frame.accel_z_g)
        if not all(math.isfinite(value) for value in (*gyro, *accel)):
            self._reset_idle()
            return None
        norm = math.sqrt(_dot(accel, accel))
        if self._last_timestamp_ms is not None:
            delta = (frame.timestamp_ms - self._last_timestamp_ms) & 0xFFFFFFFF
            if delta == 0:
                return None
            if frame.timestamp_ms < self._last_timestamp_ms and self._last_timestamp_ms - frame.timestamp_ms < 1000:
                return None
            # Do not integrate an invented duration across BLE loss/reboot.
            if delta > self.IDLE_MAX_SAMPLE_GAP_MS:
                self._reset_idle()
            if delta > self.MAX_SAMPLE_GAP_MS:
                if delta > 3000:
                    self.reset()
                else:
                    # Brief packet gaps must not teleport the grip to center.
                    # Keep the attitude and calibration; skip unknown motion.
                    self._last_timestamp_ms = frame.timestamp_ms
                    self._recent.clear()
                    self._recent.append(frame)
                    self._gyro_filter.clear()
                    self._gyro_filter.extend([gyro, gyro])
                    return 0.0, False
        else:
            delta = 0
        if self._last_timestamp_ms is None:
            if norm < 0.5:
                return None
            try:
                self._mounting_right(accel)
            except ValueError:
                # A vertical boot pose must not crash BLE or create a random basis.
                return 0.0, False
            self._q = _gravity_orientation(accel)
            self._set_neutral(accel)
            self._last_timestamp_ms = frame.timestamp_ms
            self._recent.append(frame)
            self._gyro_filter.extend([gyro, gyro])
            return 0.0, False
        self._last_timestamp_ms = frame.timestamp_ms
        self._recent.append(frame)
        while self._recent and ((frame.timestamp_ms - self._recent[0].timestamp_ms) & 0xFFFFFFFF) > self.CALIBRATION_WINDOW_MS:
            self._recent.popleft()
        dt = delta / 1000.0
        self._gyro_filter.append(gyro)
        corrected = tuple(statistics.median(v[i] for v in self._gyro_filter) - self._bias[i] for i in range(3))
        speed = math.sqrt(_dot(corrected, corrected))
        moving = speed >= self.MOTION_GYRO_THRESHOLD_DPS
        # Acceleration alone never moves the anchor. Gravity corrects attitude
        # only when its magnitude and direction agree with integrated rotation.
        rates = tuple(math.radians(v) if abs(v) >= self.GYRO_DEADBAND_DPS else 0.0 for v in corrected)
        if 0.92 <= norm <= 1.08 and 0.25 <= speed <= 120.0:
            measured = _unit(accel)
            predicted = _rotate(_conjugate(self._q), (0.0, 0.0, 1.0))
            error = _cross(measured, predicted)
            if _dot(measured, predicted) > math.cos(math.radians(8.0)):
                rates = tuple(rates[i] + self.GRAVITY_CORRECTION_GAIN * error[i] for i in range(3))
        omega = math.sqrt(_dot(rates, rates))
        if omega > 1e-9:
            half = omega * dt * 0.5
            dq = (math.cos(half), *(v * math.sin(half) / omega for v in rates))
            self._q = _normalize(_multiply(self._q, dq))
        relative = _multiply(_conjugate(self._neutral), self._q)
        self._rotation_vector = _continuous_rotation_vector(relative, self._rotation_vector)
        horizontal, vertical, twist = (angle-zero for angle, zero in
                                       zip(self._pointing_angles(), self._display_zero))
        activity = _clamp(max(speed / 900.0, abs(norm - 1.0) / 1.8), 0.0, 1.0)
        self._pose = SensorPose(
            _clamp(horizontal / self.HORIZONTAL_RANGE_DEGREES, -1.0, 1.0) * self.MAX_OFFSET_X_PX,
            _clamp(vertical / self.VERTICAL_RANGE_DEGREES, -1.0, 1.0) * self.MAX_OFFSET_Y_PX,
            _clamp(twist, -self.MAX_ANGLE_DEGREES, self.MAX_ANGLE_DEGREES),
            activity, moving,
        )
        if auto_center:
            if not self._trim_requested_bias(delta):
                self._auto_center_if_idle(delta, speed, norm)
        return activity, moving
