import base64
import struct

import pytest

from codex_whip.calibration import LearningSample
from codex_whip.models import (
    AudioChunk,
    AudioEnd,
    AudioStart,
    DeviceMessage,
    RawMotionBatch,
    RawMotionFrame,
    WhipEvent,
)
from codex_whip.protocol import (
    LineDecoder, ProtocolError, crc16_ccitt, parse_line, parse_voice_frame,
)


def binary_audio_frame(session=3, sequence=0, samples=5, predictor=-12,
                       step_index=7, payload=b"\x12\x34"):
    body = struct.pack('<BBHIIHhB', 1, 1, 13 + len(payload), session,
                       sequence, samples, predictor, step_index) + payload
    return b'\xA5\x5A' + body + struct.pack('<H', crc16_ccitt(body))


def test_parse_whip_event() -> None:
    assert parse_line("WHIP,7,1234.5,3.25,91") == WhipEvent(
        sequence=7,
        peak_gyro_dps=1234.5,
        peak_accel_g=3.25,
        duration_ms=91,
    )


def test_raw5_preserves_small_rotation_and_actual_sample_times():
    payload = b''.join(struct.pack('<Hhhhhhh', dt, 7, -23, 1, 2, 999, -4)
                       for dt in (0, 9, 20, 34))
    message = parse_line('RAW5,5,1000,' + base64.b64encode(payload).decode())
    assert [f.timestamp_ms for f in message.frames] == [1000, 1009, 1020, 1034]
    assert message.frames[0].gyro_x_dps == 0.7
    assert message.frames[0].gyro_y_dps == -2.3
    assert message.frames[0].accel_y_g == 0.999


@pytest.mark.parametrize('times', [(1,), (0, 0), (0, 20, 10), (0, 251)])
def test_raw5_rejects_invalid_timing(times):
    payload = b''.join(struct.pack('<Hhhhhhh', dt, 0, 0, 0, 0, 1000, 0) for dt in times)
    with pytest.raises(ProtocolError, match='timestamps'):
        parse_line('RAW5,1,100,' + base64.b64encode(payload).decode())


def test_raw5_split_notifications_reassemble():
    payload = struct.pack('<Hhhhhhh', 0, 3, 2, 1, 1000, 0, 0)
    line = ('RAW5,1,100,' + base64.b64encode(payload).decode() + '\n').encode()
    decoder = LineDecoder()
    found = []
    for start in range(0, len(line), 7):
        found.extend(decoder.feed(line[start:start+7]))
    assert len(found) == 1
    assert found[0].frames[0].gyro_x_dps == 0.3


def test_parse_whip2_event_with_shape_metrics() -> None:
    assert parse_line("WHIP2,9,1320.5,4.25,146,97.4,0.812,0.673,31,245.8") == WhipEvent(
        sequence=9,
        peak_gyro_dps=1320.5,
        peak_accel_g=4.25,
        duration_ms=146,
        angular_travel_deg=97.4,
        direction_consistency=0.812,
        dominant_axis_ratio=0.673,
        peak_gap_ms=31,
        peak_jerk_gps=245.8,
    )


def test_parse_voice_audio_messages() -> None:
    assert parse_line("VOICE,START,3,16000,IMA_ADPCM4") == AudioStart(
        3, 16000, "IMA_ADPCM4"
    )
    assert parse_line("AUD1,3,0,3,0,0,EQ==") == AudioChunk(
        3, 0, 3, 0, 0, b"\x11"
    )
    assert parse_line("VOICE,END,3,3,SILENCE") == AudioEnd(3, 3, "SILENCE")


def test_binary_voice_frame_is_checksummed_and_flow_controlled() -> None:
    frame = binary_audio_frame()
    assert parse_voice_frame(frame) == AudioChunk(
        3, 0, 5, -12, 7, b"\x12\x34", True
    )
    damaged = bytearray(frame)
    damaged[-3] ^= 1
    with pytest.raises(ProtocolError, match='checksum'):
        parse_voice_frame(damaged)


def test_decoder_reassembles_mixed_text_and_binary_voice_fragments() -> None:
    data = b'VOICE,START,3,16000,IMA_ADPCM4\n' + binary_audio_frame() + b'VOICE,END,3,5,SILENCE\n'
    decoder = LineDecoder()
    actual = []
    for offset in range(0, len(data), 11):
        actual.extend(decoder.feed(data[offset:offset + 11]))
    assert actual == [
        AudioStart(3, 16000, 'IMA_ADPCM4'),
        AudioChunk(3, 0, 5, -12, 7, b"\x12\x34", True),
        AudioEnd(3, 5, 'SILENCE'),
    ]


def test_parse_raw3_batch() -> None:
    payload = struct.pack(
        "<hhhhhhhhhhhh", 1234, -20, 7, 1000, -250, 0, 1300, 10, -3, 980, 5, -10
    )
    encoded = base64.b64encode(payload).decode("ascii")
    assert parse_line(f"RAW3,7,1000,{encoded}") == RawMotionBatch(
        7,
        1000,
        (
            RawMotionFrame(1000, 123.4, -2.0, 0.7, 1.0, -0.25, 0.0),
            RawMotionFrame(1010, 130.0, 1.0, -0.3, 0.98, 0.005, -0.01),
        ),
    )


def test_parse_raw4_compact_batch() -> None:
    payload = struct.pack("<bbbbbbbbbbbb", 10, -2, 1, 8, -2, 0, 20, 1, -1, 7, 0, -8)
    encoded = base64.b64encode(payload).decode("ascii")
    assert parse_line(f"RAW4,8,2000,{encoded}") == RawMotionBatch(
        8,
        2000,
        (
            RawMotionFrame(2000, 160.0, -32.0, 16.0, 1.0, -0.25, 0.0),
            RawMotionFrame(2010, 320.0, 16.0, -16.0, 0.875, 0.0, -1.0),
        ),
    )


def test_parse_learning_sample() -> None:
    assert parse_line(
        "SAMPLE3,4,CAPTURED,812.5,1.42,188,72.5,0.410,0.620,31,210.4"
    ) == LearningSample(
        sequence=4,
        result="CAPTURED",
        peak_gyro_dps=812.5,
        peak_dynamic_accel_g=1.42,
        duration_ms=188,
        angular_travel_deg=72.5,
        direction_consistency=0.41,
        dominant_axis_ratio=0.62,
        peak_gap_ms=31,
        peak_jerk_gps=210.4,
    )


def test_parse_status_line() -> None:
    assert parse_line("PONG,0.1.0") == DeviceMessage(
        kind="PONG", fields=("0.1.0",), raw="PONG,0.1.0"
    )


def test_decoder_reassembles_ble_fragments() -> None:
    decoder = LineDecoder()
    assert decoder.feed(b"WHIP,8,900") == []
    assert decoder.feed(b".0,2.7,110\r\nPONG,0.1.0\n") == [
        WhipEvent(8, 900.0, 2.7, 110),
        DeviceMessage("PONG", ("0.1.0",), "PONG,0.1.0"),
    ]


def test_decoder_reassembles_long_whip2_ble_fragments() -> None:
    decoder = LineDecoder()
    chunks = (
        b"WHIP2,10,1450.2,",
        b"4.60,172,116.8,0.7",
        b"42,0.618,27,312.5\n",
    )
    messages = []
    for chunk in chunks:
        messages.extend(decoder.feed(chunk))
    assert messages == [
        WhipEvent(10, 1450.2, 4.60, 172, 116.8, 0.742, 0.618, 27, 312.5)
    ]


@pytest.mark.parametrize(
    "line",
    [
        "WHIP,1,nan,2,100",
        "WHIP,1,200,-1,100",
        "WHIP,-1,200,2,100",
        "WHIP,1,200,2,99999",
        "WHIP,1,2",
        "WHIP2,1,900,3,120,80,1.1,0.6,20,100",
        "WHIP2,1,900,3,120,80,0.8,0.2,-1,100",
        "WHIP2,1,900,3,120,80,0.8,0.6,20,nan",
        "WHIP2,1,900,3,120,80,0.8",
        "SAMPLE3,1,CAPTURED,900",
        "SAMPLE3,1,CAPTURED,900,nan,120,80,0.8,0.6,20,100",
    ],
)
def test_invalid_whip_event_is_rejected(line: str) -> None:
    with pytest.raises(ProtocolError):
        parse_line(line)


def test_decoder_includes_the_raw_line_in_protocol_errors() -> None:
    with pytest.raises(ProtocolError, match=r"raw='WHIP,1,broken'"):
        LineDecoder().feed(b"WHIP,1,broken\n")
