import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from codex_whip.models import AudioChunk, AudioEnd, AudioStart, RawMotionBatch, RawMotionFrame
from codex_whip.voice import (
    DoubleTapDetector,
    VAD_MODEL_NAME,
    VAD_MODEL_SHA256,
    VAD_MODEL_SIZE,
    VoiceAudioAssembler,
    VoiceModule,
    VoiceSettings,
    VoiceSettingsStore,
    WhisperCppTranscriber,
    decode_ima_adpcm_chunk,
    load_voice_settings,
    sanitize_voice_transcript,
    to_simplified_chinese,
)


def test_legacy_virtual_microphone_setting_migrates_to_recognition(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text('{"schema_version":1,"input_mode":"virtual_microphone","recording_gain":3}')
    restored = load_voice_settings(path)
    assert restored.input_mode == "transcription"
    assert restored.recording_gain == 3
    with pytest.raises(ValueError, match="输入方式"):
        VoiceSettings(input_mode="virtual_microphone").validated()


def _frame(timestamp: int, dynamic: float = 0.0, gyro: float = 0.0) -> RawMotionFrame:
    return RawMotionFrame(timestamp, gyro, 0, 0, 0, 0, 1 + dynamic)


def _double_tap_frames(offset: int = 0) -> tuple[RawMotionFrame, ...]:
    frames: list[RawMotionFrame] = []
    frames.extend(_frame(offset + time) for time in range(0, 310, 10))
    frames.extend(
        (
            _frame(offset + 310, 2.2, 120),
            _frame(offset + 320, 2.8, 160),
            _frame(offset + 330),
        )
    )
    frames.extend(_frame(offset + time) for time in range(340, 550, 10))
    frames.extend(
        (
            _frame(offset + 550, 2.4, 140),
            _frame(offset + 560, 3.0, 180),
            _frame(offset + 570),
        )
    )
    return tuple(frames)


def _foreign_double_peak_frames(offset: int = 0) -> tuple[RawMotionFrame, ...]:
    frames = list(_double_tap_frames(offset))
    return tuple(
        _frame(frame.timestamp_ms, 0.45, 1250.0)
        if offset + 410 <= frame.timestamp_ms <= offset + 470
        else frame
        for frame in frames
    )


def test_double_tap_detector_accepts_two_short_settled_impacts() -> None:
    detector = DoubleTapDetector(VoiceSettings(enabled=True))
    event = detector.feed_batch(RawMotionBatch(1, 0, _double_tap_frames()))

    assert event is not None
    assert event.interval_ms == 240
    assert event.first_peak_dynamic_accel_g == pytest.approx(2.8)
    assert event.second_peak_dynamic_accel_g == pytest.approx(3.0)
    assert detector.suppress_whip


def test_double_tap_detector_rejects_a_single_table_impact() -> None:
    detector = DoubleTapDetector(VoiceSettings(enabled=True))
    frames = tuple(_frame(time) for time in range(0, 310, 10)) + (
        _frame(310, 3.0, 120),
        _frame(320),
    )

    assert detector.feed_batch(RawMotionBatch(1, 0, frames)) is None


def test_ima_adpcm_decoder_uses_low_nibble_first() -> None:
    chunk = AudioChunk(1, 0, 3, 0, 0, b"\x11")

    decoded = decode_ima_adpcm_chunk(chunk)
    values = [
        int.from_bytes(decoded[index:index + 2], "little", signed=True)
        for index in range(0, 6, 2)
    ]

    assert values == [0, 1, 2]


def test_audio_assembler_rejects_a_missing_packet() -> None:
    assembler = VoiceAudioAssembler()
    assembler.begin(AudioStart(7, 16000, "IMA_ADPCM4"))
    assembler.add(AudioChunk(7, 1, 3, 0, 0, b"\x11"))

    with pytest.raises(ValueError, match="数据包缺失"):
        assembler.finish(AudioEnd(7, 3, "SILENCE"))


def test_voice_settings_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "voice.json"
    store = VoiceSettingsStore(path)
    expected = replace(store.settings, enabled=True, impact_dynamic_accel_g=1.7)

    store.update(expected)

    assert load_voice_settings(path) == expected


def test_windows_conversion_normalizes_transcript_to_simplified_chinese() -> None:
    assert to_simplified_chinese("繼續完成當前任務") == "继续完成当前任务"


@pytest.mark.parametrize(
    "transcript",
    (
        "请关注",
        "点关注",
        "字幕说话人",
        "[音乐]",
        "（掌声）",
        "谢谢观看",
        "谢谢观看谢谢观看",
    ),
)
def test_silence_hallucinations_become_blank(transcript: str) -> None:
    assert sanitize_voice_transcript(transcript) == ""


@pytest.mark.parametrize(
    ("transcript", "expected"),
    (
        ("说话人 1：继续当前任务", "继续当前任务"),
        ("字幕说话人：先解决最关键的问题", "先解决最关键的问题"),
        ("创建一个提醒我关注项目进度的任务", "创建一个提醒我关注项目进度的任务"),
        ("先记录谢谢观看", "先记录谢谢观看"),
        ("继续当前任务。谢谢观看", "继续当前任务"),
    ),
)
def test_transcript_cleanup_preserves_real_commands(
    transcript: str,
    expected: str,
) -> None:
    assert sanitize_voice_transcript(transcript) == expected


def test_bundled_vad_model_is_verified_and_copied_to_ascii_runtime(
    tmp_path: Path,
) -> None:
    transcriber = WhisperCppTranscriber(tmp_path)

    assert transcriber.bundled_vad_model_path.name == VAD_MODEL_NAME
    assert transcriber.bundled_vad_model_path.stat().st_size == VAD_MODEL_SIZE
    transcriber._prepare_vad_model()

    assert transcriber.vad_model_path == tmp_path / VAD_MODEL_NAME
    assert transcriber.vad_model_path.stat().st_size == VAD_MODEL_SIZE
    assert hashlib.sha256(transcriber.vad_model_path.read_bytes()).hexdigest() == VAD_MODEL_SHA256


class _FakeTranscriber:
    ready = True

    def transcribe(self, _sample_rate: int, _pcm: bytes) -> str:
        return "继续完成当前任务"


class _EmptyTranscriber:
    ready = True

    def transcribe(self, _sample_rate: int, _pcm: bytes) -> str:
        return ""


@pytest.mark.parametrize('result', ['', '继续', RuntimeError('not ready')])
def test_immediate_transcription_never_flashes_recognizing(tmp_path, result):
    from unittest.mock import Mock
    emitted = []
    transcriber = Mock()
    if isinstance(result, Exception):
        transcriber.transcribe.side_effect = result
    else:
        transcriber.transcribe.return_value = result
    module = VoiceModule(VoiceSettingsStore(tmp_path/'voice.json'),
                         lambda kind,payload: emitted.append((kind,payload)), transcriber)
    module.assembler.begin(AudioStart(4,16000,'IMA_ADPCM4'))
    for sequence in range(25):
        module.assembler.add(AudioChunk(4,sequence,320,0,0,bytes(160)))
    asyncio.run(module.handle_audio(AudioEnd(4,8000,'SILENCE')))
    assert not any(k=='voice_state' and p['state']=='recognizing' for k,p in emitted)


@pytest.mark.parametrize('notify', [False, True])
def test_only_started_pending_model_shows_recognition(tmp_path, notify):
    import time
    from codex_whip.voice import WhisperCppTranscriber
    class Delayed(WhisperCppTranscriber):
        def transcribe(self, rate, pcm, *, on_started=None):
            if notify:
                on_started()
            time.sleep(.35)
            return ''
    emitted = []
    module = VoiceModule(VoiceSettingsStore(tmp_path/'voice.json'),
                         lambda k,p: emitted.append((k,p)), Delayed())
    module.assembler.begin(AudioStart(4,16000,'IMA_ADPCM4'))
    for sequence in range(25):
        module.assembler.add(AudioChunk(4,sequence,320,0,0,bytes(160)))
    asyncio.run(module.handle_audio(AudioEnd(4,8000,'SILENCE')))
    states = [p['state'] for k,p in emitted if k=='voice_state']
    assert ('recognizing' in states) is notify
    assert states[-1] == 'empty'


@pytest.mark.parametrize('reason,samples', [('TX_FAILED',8000), ('SILENCE',320), ('SILENCE',8001)])
def test_invalid_recording_never_enters_recognizing(tmp_path, reason, samples):
    from unittest.mock import Mock
    emitted = []
    transcriber = Mock()
    module = VoiceModule(VoiceSettingsStore(tmp_path/'voice.json'),
                         lambda kind,payload: emitted.append((kind,payload)), transcriber)
    asyncio.run(module.handle_audio(AudioStart(4,16000,'IMA_ADPCM4')))
    for sequence in range(25 if samples >= 8000 else 1):
        module.assembler.add(AudioChunk(4,sequence,320,0,0,bytes(160)))
    asyncio.run(module.handle_audio(AudioEnd(4,samples,reason)))
    assert not any(kind=='voice_state' and payload['state']=='recognizing' for kind,payload in emitted)
    assert any(kind=='voice_error' for kind,payload in emitted)
    transcriber.transcribe.assert_not_called()


def test_voice_transcript_is_kept_until_successful_send(tmp_path: Path) -> None:
    emitted: list[tuple[str, object]] = []
    store = VoiceSettingsStore(tmp_path / "voice.json")
    module = VoiceModule(
        store,
        lambda kind, payload: emitted.append((kind, payload)),
        _FakeTranscriber(),
    )
    module.assembler.begin(AudioStart(4, 16000, "IMA_ADPCM4"))
    payload = bytes([0] * 160)
    for sequence in range(25):
        module.assembler.add(AudioChunk(4, sequence, 320, 0, 0, payload))

    asyncio.run(module.handle_audio(AudioEnd(4, 8000, "SILENCE")))

    assert module.pending_text == "继续完成当前任务"
    module.mark_sent("别的内容")
    assert module.pending_text == "继续完成当前任务"
    module.mark_sent("继续完成当前任务")
    assert module.pending_text is None


def test_new_recording_discards_the_previous_voice_candidate(tmp_path: Path) -> None:
    emitted: list[tuple[str, object]] = []
    module = VoiceModule(
        VoiceSettingsStore(tmp_path / "voice.json"),
        lambda kind, payload: emitted.append((kind, payload)),
        _FakeTranscriber(),
    )
    module.set_pending("旧的语音候选")

    asyncio.run(module.handle_audio(AudioStart(8, 16000, "IMA_ADPCM4")))

    assert module.pending_text is None
    assert ("voice_pending", None) in emitted
    assert any(
        kind == "voice_state"
        and payload["state"] == "recording"
        and payload["replacing"] is True
        for kind, payload in emitted
    )


def test_no_detected_speech_leaves_no_candidate_or_error(tmp_path: Path) -> None:
    emitted: list[tuple[str, object]] = []
    module = VoiceModule(
        VoiceSettingsStore(tmp_path / "voice.json"),
        lambda kind, payload: emitted.append((kind, payload)),
        _EmptyTranscriber(),
    )
    module.set_pending("旧的语音候选")
    asyncio.run(module.handle_audio(AudioStart(9, 16000, "IMA_ADPCM4")))
    payload = bytes([0] * 160)
    for sequence in range(25):
        asyncio.run(module.handle_audio(AudioChunk(9, sequence, 320, 0, 0, payload)))

    asyncio.run(module.handle_audio(AudioEnd(9, 8000, "SILENCE")))

    assert module.pending_text is None
    assert any(
        kind == "voice_state" and payload["state"] == "empty"
        for kind, payload in emitted
    )
    assert not any(kind == "voice_error" for kind, _payload in emitted)


def test_legacy_double_tap_learning_no_longer_controls_runtime_range(tmp_path: Path) -> None:
    emitted: list[tuple[str, object]] = []
    store = VoiceSettingsStore(tmp_path / "voice.json")
    store.update(replace(store.settings, enabled=True, impact_dynamic_accel_g=8.0))
    module = VoiceModule(store, lambda kind, payload: emitted.append((kind, payload)))

    module.start_calibration(5)
    for index in range(5):
        frames = _double_tap_frames(index * 2500)
        triggered, suppressed = module.feed_motion(RawMotionBatch(index, frames[0].timestamp_ms, frames))
        assert not triggered
        assert suppressed
        assert [
            payload["done"]
            for kind, payload in emitted
            if kind == "voice_calibration" and payload["done"] > 0
        ] == list(range(1, index + 1))

        event = module.record_calibration_sample()
        assert event.interval_ms == 240
        if index == 0:
            with pytest.raises(ValueError, match="原始数据不足|没有找到"):
                module.record_calibration_sample()

    assert not module.calibration_active
    assert module.double_tap_template_ready
    assert module.double_tap_profile_path.is_file()
    assert store.settings.impact_dynamic_accel_g < 8.0
    assert store.settings.min_interval_ms == 120
    assert store.settings.max_interval_ms == 420
    assert store.settings.max_tap_gyro_dps == 2000.0
    assert not any(kind == "voice_trigger" for kind, _payload in emitted)

    restored = VoiceModule(store, lambda kind, payload: emitted.append((kind, payload)))
    assert restored.double_tap_template_ready
    frames = _double_tap_frames(12_500)
    triggered, suppressed = restored.feed_motion(
        RawMotionBatch(9, frames[0].timestamp_ms, frames)
    )
    assert not triggered
    assert not suppressed
    assert restored.handle_hardware_double_tap(frames[-1].timestamp_ms)
    assert not any(kind == "voice_match" for kind, _payload in emitted)


def test_runtime_ignores_legacy_trajectory_when_force_is_in_range(tmp_path: Path) -> None:
    emitted: list[tuple[str, object]] = []
    store = VoiceSettingsStore(tmp_path / "voice.json")
    store.update(replace(store.settings, enabled=True))
    module = VoiceModule(store, lambda kind, payload: emitted.append((kind, payload)))
    module.start_calibration(5)
    for index in range(5):
        frames = _double_tap_frames(index * 2500)
        module.feed_motion(RawMotionBatch(index, frames[0].timestamp_ms, frames))
        module.record_calibration_sample()

    candidate = _foreign_double_peak_frames(12_500)
    triggered, suppressed = module.feed_motion(
        RawMotionBatch(10, candidate[0].timestamp_ms, candidate)
    )

    assert not triggered
    assert not suppressed
    assert not any(kind == "voice_match" for kind, _payload in emitted)
    assert not any(kind == "voice_trigger" for kind, _payload in emitted)
    assert module.handle_hardware_double_tap(candidate[-1].timestamp_ms)
    assert any(kind == "voice_trigger" for kind, _payload in emitted)


def test_manual_double_tap_learning_rejects_a_single_impact(tmp_path: Path) -> None:
    store = VoiceSettingsStore(tmp_path / "voice.json")
    store.update(replace(store.settings, enabled=True))
    module = VoiceModule(store, lambda _kind, _payload: None)
    module.start_calibration(5)
    frames = tuple(_frame(time) for time in range(0, 310, 10)) + (
        _frame(310, 3.0, 120),
        _frame(320),
    )
    module.feed_motion(RawMotionBatch(1, 0, frames))

    with pytest.raises(ValueError, match="两次|数据不足"):
        module.record_calibration_sample()
