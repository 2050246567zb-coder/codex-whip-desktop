"""Explicit stationary gyro measurements, scoped to a BLE device identity.

Never infer bias from an arbitrary held pose: slow yaw and gyro bias cannot
be distinguished with a six-axis IMU alone. These profiles require a known
stationary capture, not the three-second automatic centering gesture.
"""
import json
import math
from pathlib import Path


def load_sensor_bias(path: Path, identity: str) -> tuple[float, float, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1:
            return (0.0, 0.0, 0.0)
        values = data["devices"][identity.strip().upper()]["gyro_bias_dps"]
        if (len(values) != 3 or any(type(v) not in (float, int)
                                  or not math.isfinite(v) for v in values)
                or math.sqrt(sum(v*v for v in values)) > 15):
            return (0.0, 0.0, 0.0)
        return tuple(float(v) for v in values)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return (0.0, 0.0, 0.0)
