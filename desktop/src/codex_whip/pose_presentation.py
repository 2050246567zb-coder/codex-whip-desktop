"""Bounded display-only continuation between irregular IMU notifications.

Never feed predicted motion back into calibration or gesture recognition.
Long gaps and burst deliveries do not provide a trustworthy velocity.
"""
from dataclasses import replace
import math

from .sensor_pose import SensorPose


class PoseContinuation:
    MAX_HORIZON = .080
    MAX_DISTANCE = 24.0
    MAX_ANGLE = 5.0

    def __init__(self):
        self.reset()

    def reset(self):
        self.pose = None
        self.received_at = 0.0
        self.velocity = (0.0, 0.0, 0.0)
        self.horizon = .040

    def push(self, pose: SensorPose, now: float):
        previous = self.pose
        dt = now - self.received_at
        self.velocity = (0.0, 0.0, 0.0)
        if (previous is not None and pose.moving and previous.moving
                and not pose.auto_centered and not previous.auto_centered
                and .008 <= dt <= .150):
            self.horizon = min(self.MAX_HORIZON, max(.040, dt))
            vx = (pose.offset_x - previous.offset_x) / dt
            vy = (pose.offset_y - previous.offset_y) / dt
            va = (pose.angle_degrees - previous.angle_degrees) / dt
            # Integrated linear deceleration travels v * horizon / 2.
            max_speed = 2 * self.MAX_DISTANCE / self.horizon
            scale = min(1.0, max_speed / max(math.hypot(vx, vy), 1e-9))
            angle_speed = 2 * self.MAX_ANGLE / self.horizon
            self.velocity = (vx * scale, vy * scale,
                             max(-angle_speed, min(angle_speed, va)))
        self.pose = pose
        self.received_at = now

    def sample(self, now: float) -> SensorPose | None:
        if self.pose is None:
            return None
        elapsed = max(0.0, min(self.horizon, now - self.received_at))
        # Arrive with the measured velocity, then gently stop. Beyond the
        # horizon hold this bounded endpoint, never drift or snap backwards.
        travel_time = elapsed - elapsed * elapsed / (2 * self.horizon)
        vx, vy, va = self.velocity
        return replace(self.pose,
                       offset_x=self.pose.offset_x + vx * travel_time,
                       offset_y=self.pose.offset_y + vy * travel_time,
                       angle_degrees=self.pose.angle_degrees + va * travel_time,
                       auto_centered=False)
