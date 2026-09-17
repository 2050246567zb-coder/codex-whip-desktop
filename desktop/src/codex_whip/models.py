from __future__ import annotations

from dataclasses import dataclass

from .calibration import LearningSample


@dataclass(frozen=True, slots=True)
class WhipEvent:
    sequence: int
    peak_gyro_dps: float
    peak_accel_g: float
    duration_ms: int
    angular_travel_deg: float | None = None
    direction_consistency: float | None = None
    dominant_axis_ratio: float | None = None
    peak_gap_ms: int | None = None
    peak_jerk_gps: float | None = None


@dataclass(frozen=True, slots=True)
class DeviceMessage:
    kind: str
    fields: tuple[str, ...]
    raw: str


@dataclass(frozen=True, slots=True)
class RawMotionFrame:
    timestamp_ms: int
    gyro_x_dps: float
    gyro_y_dps: float
    gyro_z_dps: float
    accel_x_g: float
    accel_y_g: float
    accel_z_g: float


@dataclass(frozen=True, slots=True)
class RawMotionBatch:
    sequence: int
    start_timestamp_ms: int
    frames: tuple[RawMotionFrame, ...]


@dataclass(frozen=True, slots=True)
class AudioStart:
    session: int
    sample_rate: int
    codec: str


@dataclass(frozen=True, slots=True)
class AudioChunk:
    session: int
    sequence: int
    sample_count: int
    predictor: int
    step_index: int
    payload: bytes


@dataclass(frozen=True, slots=True)
class AudioEnd:
    session: int
    total_samples: int
    reason: str


ProtocolMessage = (
    WhipEvent
    | DeviceMessage
    | LearningSample
    | RawMotionBatch
    | AudioStart
    | AudioChunk
    | AudioEnd
)
