from __future__ import annotations

import math
import base64
import binascii
import struct

from .calibration import LearningSample
from .models import (
    AudioChunk,
    AudioEnd,
    AudioStart,
    DeviceMessage,
    ProtocolMessage,
    RawMotionBatch,
    RawMotionFrame,
    WhipEvent,
)

MAX_LINE_BYTES = 256
VOICE_FRAME_MAGIC = b"\xA5\x5A"
VOICE_FRAME_TYPE_AUDIO = 1
VOICE_FRAME_VERSION = 1
VOICE_FRAME_HEADER_BYTES = 19
VOICE_FRAME_OVERHEAD_BYTES = 21
MAX_VOICE_FRAME_BYTES = 244


class ProtocolError(ValueError):
    """Raised when a complete device line is malformed."""


def crc16_ccitt(data: bytes | bytearray | memoryview) -> int:
    """CRC-16/CCITT-FALSE used by firmware voice frames."""
    crc = 0xFFFF
    for value in data:
        crc ^= int(value) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def parse_voice_frame(frame: bytes | bytearray) -> AudioChunk:
    """Decode one self-contained binary ADPCM frame.

    The frame is deliberately self-framing and checksummed so a dropped audio
    packet can abort only the recording, never poison the following text
    protocol or force a BLE reconnect.
    """
    raw = bytes(frame)
    if len(raw) < VOICE_FRAME_OVERHEAD_BYTES or raw[:2] != VOICE_FRAME_MAGIC:
        raise ProtocolError("invalid binary voice frame")
    kind, version, payload_length = struct.unpack_from("<BBH", raw, 2)
    if kind != VOICE_FRAME_TYPE_AUDIO or version != VOICE_FRAME_VERSION:
        raise ProtocolError("unsupported binary voice frame")
    total = 6 + payload_length + 2
    if total != len(raw) or total > MAX_VOICE_FRAME_BYTES or payload_length < 14:
        raise ProtocolError("binary voice frame length is invalid")
    expected_crc = struct.unpack_from("<H", raw, total - 2)[0]
    if crc16_ccitt(raw[2:total - 2]) != expected_crc:
        raise ProtocolError("binary voice frame checksum failed")
    session, sequence, sample_count, predictor, step_index = struct.unpack_from(
        "<IIHhB", raw, 6
    )
    payload = raw[VOICE_FRAME_HEADER_BYTES:total - 2]
    if not 1 <= sample_count <= 440:
        raise ProtocolError("binary voice sample count is outside the accepted range")
    if not 0 <= step_index <= 88:
        raise ProtocolError("binary voice ADPCM state is invalid")
    if not payload or len(payload) > 220 or sample_count > len(payload) * 2 + 1:
        raise ProtocolError("binary voice payload has an invalid length")
    return AudioChunk(
        session, sequence, sample_count, predictor, step_index, payload, True
    )


def parse_line(line: str) -> ProtocolMessage:
    normalized = line.strip()
    if not normalized:
        raise ProtocolError("empty line")

    parts = normalized.split(",")
    kind = parts[0].upper()
    if kind == "AUD1":
        return _parse_audio_chunk(parts)
    if kind == "VOICE" and len(parts) >= 2 and parts[1].upper() == "START":
        return _parse_audio_start(parts)
    if kind == "VOICE" and len(parts) >= 2 and parts[1].upper() == "END":
        return _parse_audio_end(parts)
    if kind in {"RAW3", "RAW4"}:
        return _parse_raw_motion(parts, packed_8bit=kind == "RAW4")
    if kind == "RAW5":
        return _parse_precise_motion(parts)
    if kind == "SAMPLE3":
        return _parse_learning_sample(parts)
    if kind not in {"WHIP", "WHIP2"}:
        return DeviceMessage(kind=kind, fields=tuple(parts[1:]), raw=normalized)

    expected_fields = 5 if kind == "WHIP" else 10
    if len(parts) != expected_fields:
        if kind == "WHIP":
            raise ProtocolError("WHIP requires sequence, peak gyro, peak accel, duration")
        raise ProtocolError(
            "WHIP2 requires sequence, peak gyro, peak accel, duration, angular "
            "travel, direction consistency, dominant axis, peak gap, peak jerk"
        )

    try:
        sequence = int(parts[1])
        peak_gyro_dps = float(parts[2])
        peak_accel_g = float(parts[3])
        duration_ms = int(parts[4])
        angular_travel_deg = float(parts[5]) if kind == "WHIP2" else None
        direction_consistency = float(parts[6]) if kind == "WHIP2" else None
        dominant_axis_ratio = float(parts[7]) if kind == "WHIP2" else None
        peak_gap_ms = int(parts[8]) if kind == "WHIP2" else None
        peak_jerk_gps = float(parts[9]) if kind == "WHIP2" else None
    except ValueError as exc:
        raise ProtocolError(f"{kind} contains a non-numeric field") from exc

    if sequence < 0:
        raise ProtocolError("sequence must be non-negative")
    if not math.isfinite(peak_gyro_dps) or not 0 <= peak_gyro_dps <= 5000:
        raise ProtocolError("peak gyro is outside the accepted range")
    if not math.isfinite(peak_accel_g) or not 0 <= peak_accel_g <= 40:
        raise ProtocolError("peak accel is outside the accepted range")
    if not 0 <= duration_ms <= 5000:
        raise ProtocolError("duration is outside the accepted range")
    if angular_travel_deg is not None and (
        not math.isfinite(angular_travel_deg) or not 0 <= angular_travel_deg <= 5000
    ):
        raise ProtocolError("angular travel is outside the accepted range")
    if direction_consistency is not None and (
        not math.isfinite(direction_consistency)
        or not 0 <= direction_consistency <= 1
    ):
        raise ProtocolError("direction consistency is outside the accepted range")
    if dominant_axis_ratio is not None and (
        not math.isfinite(dominant_axis_ratio) or not 0 <= dominant_axis_ratio <= 1
    ):
        raise ProtocolError("dominant axis ratio is outside the accepted range")
    if peak_gap_ms is not None and not 0 <= peak_gap_ms <= 5000:
        raise ProtocolError("peak gap is outside the accepted range")
    if peak_jerk_gps is not None and (
        not math.isfinite(peak_jerk_gps) or not 0 <= peak_jerk_gps <= 100000
    ):
        raise ProtocolError("peak jerk is outside the accepted range")

    return WhipEvent(
        sequence=sequence,
        peak_gyro_dps=peak_gyro_dps,
        peak_accel_g=peak_accel_g,
        duration_ms=duration_ms,
        angular_travel_deg=angular_travel_deg,
        direction_consistency=direction_consistency,
        dominant_axis_ratio=dominant_axis_ratio,
        peak_gap_ms=peak_gap_ms,
        peak_jerk_gps=peak_jerk_gps,
    )


def _parse_audio_start(parts: list[str]) -> AudioStart:
    if len(parts) != 5:
        raise ProtocolError("VOICE START requires session, sample rate, codec")
    try:
        session = int(parts[2])
        sample_rate = int(parts[3])
    except ValueError as exc:
        raise ProtocolError("VOICE START contains a non-numeric field") from exc
    codec = parts[4].strip().upper()
    if session < 0:
        raise ProtocolError("VOICE START session must be non-negative")
    if sample_rate != 16000 or codec != "IMA_ADPCM4":
        raise ProtocolError("VOICE START uses an unsupported audio format")
    return AudioStart(session, sample_rate, codec)


def _parse_audio_chunk(parts: list[str]) -> AudioChunk:
    if len(parts) != 7:
        raise ProtocolError(
            "AUD1 requires session, sequence, sample count, predictor, step index, payload"
        )
    try:
        session = int(parts[1])
        sequence = int(parts[2])
        sample_count = int(parts[3])
        predictor = int(parts[4])
        step_index = int(parts[5])
        payload = base64.b64decode(parts[6], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("AUD1 contains an invalid field") from exc
    if session < 0 or sequence < 0:
        raise ProtocolError("AUD1 counters must be non-negative")
    if not 1 <= sample_count <= 320:
        raise ProtocolError("AUD1 sample count is outside the accepted range")
    if not -32768 <= predictor <= 32767 or not 0 <= step_index <= 88:
        raise ProtocolError("AUD1 ADPCM state is invalid")
    if not payload or len(payload) > 160 or sample_count > len(payload) * 2 + 1:
        raise ProtocolError("AUD1 payload has an invalid length")
    return AudioChunk(
        session, sequence, sample_count, predictor, step_index, payload
    )


def _parse_audio_end(parts: list[str]) -> AudioEnd:
    if len(parts) != 5:
        raise ProtocolError("VOICE END requires session, sample count, reason")
    try:
        session = int(parts[2])
        total_samples = int(parts[3])
    except ValueError as exc:
        raise ProtocolError("VOICE END contains a non-numeric field") from exc
    reason = parts[4].strip().upper()
    if session < 0 or not 0 <= total_samples <= 16_000 * 30:
        raise ProtocolError("VOICE END counters are outside the accepted range")
    if not reason or len(reason) > 24:
        raise ProtocolError("VOICE END reason is invalid")
    return AudioEnd(session, total_samples, reason)


def _parse_raw_motion(parts: list[str], *, packed_8bit: bool = False) -> RawMotionBatch:
    if len(parts) != 4:
        raise ProtocolError("RAW3 requires sequence, start timestamp, payload")
    try:
        sequence = int(parts[1])
        start_timestamp_ms = int(parts[2])
        payload = base64.b64decode(parts[3], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("RAW3 contains an invalid field") from exc
    if sequence < 0 or start_timestamp_ms < 0:
        raise ProtocolError("RAW3 counters must be non-negative")
    sample_bytes = 6 if packed_8bit else 12
    if not payload or len(payload) % sample_bytes != 0 or len(payload) > 96:
        raise ProtocolError("RAW3 payload has an invalid length")

    frames: list[RawMotionFrame] = []
    sample_format = "<bbbbbb" if packed_8bit else "<hhhhhh"
    for index, values in enumerate(struct.iter_unpack(sample_format, payload)):
        gx, gy, gz, ax, ay, az = values
        gyro_values = (
            (gx * 16.0, gy * 16.0, gz * 16.0)
            if packed_8bit
            else (gx / 10.0, gy / 10.0, gz / 10.0)
        )
        accel_values = (
            (ax * 0.125, ay * 0.125, az * 0.125)
            if packed_8bit
            else (ax / 1000.0, ay / 1000.0, az / 1000.0)
        )
        timestamp_offset = round(index * 1000 / 104)
        frames.append(
            RawMotionFrame(
                timestamp_ms=start_timestamp_ms + timestamp_offset,
                gyro_x_dps=gyro_values[0],
                gyro_y_dps=gyro_values[1],
                gyro_z_dps=gyro_values[2],
                accel_x_g=accel_values[0],
                accel_y_g=accel_values[1],
                accel_z_g=accel_values[2],
            )
        )
    return RawMotionBatch(sequence, start_timestamp_ms, tuple(frames))


def _parse_precise_motion(parts: list[str]) -> RawMotionBatch:
    if len(parts) != 4:
        raise ProtocolError("RAW5 requires sequence, start timestamp, payload")
    try:
        sequence, start = int(parts[1]), int(parts[2])
        payload = base64.b64decode(parts[3], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ProtocolError("RAW5 contains an invalid field") from exc
    if sequence < 0 or not 0 <= start <= 0xFFFFFFFF:
        raise ProtocolError("RAW5 counters are outside the accepted range")
    if not payload or len(payload) % 14 or len(payload) > 56:
        raise ProtocolError("RAW5 payload has an invalid length")
    frames = []
    previous = -1
    for delta, gx, gy, gz, ax, ay, az in struct.iter_unpack("<Hhhhhhh", payload):
        if delta <= previous or delta > 250 or (previous == -1 and delta != 0):
            raise ProtocolError("RAW5 timestamps must start at zero and increase")
        previous = delta
        frames.append(RawMotionFrame(
            (start + delta) & 0xFFFFFFFF,
            gx / 10.0, gy / 10.0, gz / 10.0,
            ax / 1000.0, ay / 1000.0, az / 1000.0,
        ))
    return RawMotionBatch(sequence, start, tuple(frames))


def _parse_learning_sample(parts: list[str]) -> LearningSample:
    if len(parts) != 11:
        raise ProtocolError(
            "SAMPLE3 requires sequence, result, peak gyro, dynamic accel, "
            "duration, angular travel, direction consistency, dominant axis, "
            "peak gap, peak jerk"
        )
    try:
        sample = LearningSample(
            sequence=int(parts[1]),
            result=parts[2].strip().upper(),
            peak_gyro_dps=float(parts[3]),
            peak_dynamic_accel_g=float(parts[4]),
            duration_ms=int(parts[5]),
            angular_travel_deg=float(parts[6]),
            direction_consistency=float(parts[7]),
            dominant_axis_ratio=float(parts[8]),
            peak_gap_ms=int(parts[9]),
            peak_jerk_gps=float(parts[10]),
        )
    except ValueError as exc:
        raise ProtocolError("SAMPLE3 contains a non-numeric field") from exc

    numeric_values = (
        sample.peak_gyro_dps,
        sample.peak_dynamic_accel_g,
        sample.angular_travel_deg,
        sample.direction_consistency,
        sample.dominant_axis_ratio,
        sample.peak_jerk_gps,
    )
    if sample.sequence < 0:
        raise ProtocolError("SAMPLE3 sequence must be non-negative")
    if not sample.result or len(sample.result) > 24:
        raise ProtocolError("SAMPLE3 result is invalid")
    if not all(math.isfinite(value) for value in numeric_values):
        raise ProtocolError("SAMPLE3 contains a non-finite field")
    if not 0 <= sample.peak_gyro_dps <= 5000:
        raise ProtocolError("SAMPLE3 peak gyro is outside the accepted range")
    if not 0 <= sample.peak_dynamic_accel_g <= 40:
        raise ProtocolError("SAMPLE3 dynamic accel is outside the accepted range")
    if not 0 <= sample.duration_ms <= 5000:
        raise ProtocolError("SAMPLE3 duration is outside the accepted range")
    if not 0 <= sample.angular_travel_deg <= 5000:
        raise ProtocolError("SAMPLE3 angular travel is outside the accepted range")
    if not 0 <= sample.direction_consistency <= 1:
        raise ProtocolError("SAMPLE3 direction consistency is outside the accepted range")
    if not 0 <= sample.dominant_axis_ratio <= 1:
        raise ProtocolError("SAMPLE3 dominant axis is outside the accepted range")
    if not 0 <= sample.peak_gap_ms <= 5000:
        raise ProtocolError("SAMPLE3 peak gap is outside the accepted range")
    if not 0 <= sample.peak_jerk_gps <= 100000:
        raise ProtocolError("SAMPLE3 peak jerk is outside the accepted range")
    return sample


class LineDecoder:
    """Reassemble mixed newline messages and checksummed binary voice frames."""

    def __init__(self, max_line_bytes: int = MAX_LINE_BYTES) -> None:
        self._buffer = bytearray()
        self._max_line_bytes = max_line_bytes

    def feed(self, data: bytes | bytearray) -> list[ProtocolMessage]:
        self._buffer.extend(data)
        if (not self._buffer.startswith(VOICE_FRAME_MAGIC)
                and len(self._buffer) > self._max_line_bytes
                and b"\n" not in self._buffer):
            self._buffer.clear()
            raise ProtocolError("unterminated device line exceeded maximum length")

        messages: list[ProtocolMessage] = []
        while self._buffer:
            if self._buffer.startswith(VOICE_FRAME_MAGIC):
                if len(self._buffer) < 6:
                    break
                payload_length = struct.unpack_from("<H", self._buffer, 4)[0]
                total = 6 + payload_length + 2
                if total < VOICE_FRAME_OVERHEAD_BYTES or total > MAX_VOICE_FRAME_BYTES:
                    self._buffer.clear()
                    raise ProtocolError("binary voice frame length is invalid")
                if len(self._buffer) < total:
                    break
                frame = bytes(self._buffer[:total])
                del self._buffer[:total]
                messages.append(parse_voice_frame(frame))
                continue
            if b"\n" not in self._buffer:
                if len(self._buffer) > self._max_line_bytes:
                    self._buffer.clear()
                    raise ProtocolError("unterminated device line exceeded maximum length")
                break
            raw_line, _, remainder = self._buffer.partition(b"\n")
            self._buffer = bytearray(remainder)
            raw_line = raw_line.rstrip(b"\r")
            if not raw_line:
                continue
            if len(raw_line) > self._max_line_bytes:
                raise ProtocolError("device line exceeded maximum length")
            try:
                text = raw_line.decode("ascii", errors="strict")
            except UnicodeDecodeError as exc:
                raise ProtocolError("device line is not ASCII") from exc
            try:
                messages.append(parse_line(text))
            except ProtocolError as exc:
                raise ProtocolError(f"{exc}; raw={text!r}") from exc
        return messages
