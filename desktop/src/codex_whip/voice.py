from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from collections import deque
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .models import AudioChunk, AudioEnd, AudioStart, RawMotionBatch, RawMotionFrame
from .motion_v3 import build_motion_features, dtw_distance
from .paths import user_data_dir, voice_runtime_dir


WHISPER_VERSION = "1.8.1"
MODEL_NAME = "ggml-small-q5_1.bin"
MODEL_SIZE = 190_085_487
MODEL_SHA256 = "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"
MODEL_URL = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/"
    "c521a4b02f422512d734391fdf08bb08c0862f68/ggml-small-q5_1.bin"
    "?download=true"
)
VAD_MODEL_NAME = "ggml-silero-v5.1.2.bin"
VAD_MODEL_SIZE = 885_098
VAD_MODEL_SHA256 = "29940d98d42b91fbd05ce489f3ecf7c72f0a42f027e4875919a28fb4c04ea2cf"

_NON_SPEECH_TAG_RE = re.compile(
    r"(?:<\|[^>]{1,48}\|>|"
    r"[\[【（(]\s*(?:music|applause|laughter|silence|blank_audio|"
    r"音乐|背景音乐|掌声|笑声|咳嗽|噪声|静音|无语音|"
    r"字幕(?:说话人)?|说话人\s*\d*|旁白)"
    r"[^\]】）)]{0,32}[\]】）)])",
    re.IGNORECASE,
)
_SPEAKER_PREFIX_RE = re.compile(
    r"^(?:(?:字幕(?:说话人)?|说话人\s*\d*|旁白)\s*[:：\-—]\s*)+",
    re.IGNORECASE,
)
_TRAILING_OUTRO_RE = re.compile(
    r"[，,。.!！?？；;]\s*(?:感谢|谢谢)(?:大家)?(?:的)?"
    r"(?:观看|收看|聆听)[。.!！?？]*$",
    re.IGNORECASE,
)
_FULL_HALLUCINATION_PATTERNS = (
    re.compile(r"^(?:感谢|谢谢)(?:大家)?(?:的)?(?:观看|收看|聆听)+$"),
    re.compile(
        r"^(?:请|记得|欢迎大家?|欢迎)?(?:点|点击|点赞|点个赞)?"
        r"(?:并|和)?(?:关注|订阅)(?:我的)?(?:频道|账号)?(?:吧|哦|谢谢)?$"
    ),
    re.compile(r"^(?:点赞|关注|订阅|转发|打赏|支持){2,}$"),
    re.compile(r"^(?:字幕(?:组|说话人)?|说话人\d*|旁白)(?:由.*(?:提供|制作))?$"),
)
_REPEATED_HALLUCINATIONS = (
    "谢谢观看",
    "感谢观看",
    "请关注",
    "点关注",
    "字幕说话人",
)

MANUAL_CAPTURE_WINDOW_MS = 2200
MANUAL_CAPTURE_MIN_IMPACT_G = 0.25
MANUAL_CAPTURE_MIN_INTERVAL_MS = 80
MANUAL_CAPTURE_MAX_INTERVAL_MS = 1000
MANUAL_CAPTURE_PEAK_GAP_MS = 70
DOUBLE_TAP_PROFILE_SCHEMA = 1

IMA_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
    34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
    143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
    494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
    4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
    27086, 29794, 32767,
)
IMA_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8)


@dataclass(frozen=True, slots=True)
class VoiceSettings:
    enabled: bool = False
    input_mode: str = "transcription"
    speech_provider: str = 'local'
    recording_gain: float = 2.0
    impact_dynamic_accel_g: float = 1.25
    max_tap_gyro_dps: float = 700.0
    min_interval_ms: int = 150
    max_interval_ms: int = 700
    pre_still_ms: int = 220
    settle_ms: int = 60
    max_pulse_ms: int = 120
    silence_ms: int = 1200
    max_recording_ms: int = 15000
    tap_force_calibrated: bool = False
    tap_light_g: float = 0.0
    tap_heavy_g: float = 0.0

    def validated(self) -> "VoiceSettings":
        from .cloud_speech import PRESETS
        if self.input_mode not in {"transcription", "virtual_microphone"}:
            raise ValueError("未知语音输入方式")
        if self.speech_provider != 'local' and self.speech_provider not in PRESETS:
            raise ValueError('未知语音识别服务')
        if type(self.recording_gain) not in (int, float) or not math.isfinite(self.recording_gain) or not 1 <= self.recording_gain <= 8:
            raise ValueError('录音增益必须在 1–8 倍')
        if (type(self.tap_force_calibrated) is not bool
                or not all(math.isfinite(v) and 0 <= v <= 100
                           for v in (self.tap_light_g, self.tap_heavy_g))):
            raise ValueError("敲击力度标定数据无效")
        if not 0.25 <= self.impact_dynamic_accel_g <= 12.0:
            raise ValueError("双敲冲击阈值必须在 0.25–12 g")
        if not 80 <= self.max_tap_gyro_dps <= 2000:
            raise ValueError("双敲最大角速度必须在 80–2000 dps")
        if not 80 <= self.min_interval_ms <= 500:
            raise ValueError("双敲最短间隔必须在 80–500 ms")
        if not self.min_interval_ms + 50 <= self.max_interval_ms <= 1200:
            raise ValueError("双敲最长间隔必须比最短间隔至少多 50 ms，且不超过 1200 ms")
        if not 80 <= self.pre_still_ms <= 1500:
            raise ValueError("敲击前静止时间必须在 80–1500 ms")
        if not 20 <= self.settle_ms <= 400:
            raise ValueError("两次敲击间稳定时间必须在 20–400 ms")
        if not 30 <= self.max_pulse_ms <= 250:
            raise ValueError("单次敲击最长时间必须在 30–250 ms")
        if not 400 <= self.silence_ms <= 4000:
            raise ValueError("结束录音静音时间必须在 400–4000 ms")
        if not 3000 <= self.max_recording_ms <= 30000:
            raise ValueError("最长录音时间必须在 3–30 秒")
        return self


def default_voice_settings_path() -> Path:
    return user_data_dir() / "voice-settings.json"


def load_voice_settings(path: Path) -> VoiceSettings:
    fallback = VoiceSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            return fallback
        values = {
            field: data[field]
            for field in asdict(fallback)
            if field in data
        }
        return VoiceSettings(**values).validated()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback


def save_voice_settings(path: Path, settings: VoiceSettings) -> None:
    value = settings.validated()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"schema_version": 1, **asdict(value)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class VoiceSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_voice_settings_path()
        self._settings = load_voice_settings(self.path)
        self._lock = threading.Lock()

    @property
    def settings(self) -> VoiceSettings:
        with self._lock:
            return self._settings

    def update(self, settings: VoiceSettings) -> None:
        value = settings.validated()
        save_voice_settings(self.path, value)
        with self._lock:
            self._settings = value


@dataclass(frozen=True, slots=True)
class DoubleTapEvent:
    first_peak_dynamic_accel_g: float
    second_peak_dynamic_accel_g: float
    peak_gyro_dps: float
    interval_ms: int
    first_at_ms: int = 0
    second_at_ms: int = 0


@dataclass(slots=True)
class _TapPulse:
    started_at_ms: int
    pre_still_ms: int
    peak_dynamic_accel_g: float
    peak_gyro_dps: float
    peak_at_ms: int


class DoubleTapDetector:
    STILL_DYNAMIC_G = 0.20
    STILL_GYRO_DPS = 96.0
    COOLDOWN_MS = 1400

    def __init__(self, settings: VoiceSettings | None = None) -> None:
        self.settings = (settings or VoiceSettings()).validated()
        self._still_since_ms: int | None = None
        self._pulse: _TapPulse | None = None
        self._first: _TapPulse | None = None
        self._settled_since_ms: int | None = None
        self._settled_after_first = False
        self._cooldown_until_ms = 0
        self._latest_ms = 0

    @property
    def suppress_whip(self) -> bool:
        return (
            self._pulse is not None
            or self._first is not None
            or self._latest_ms < self._cooldown_until_ms
        )

    def update_settings(self, settings: VoiceSettings) -> None:
        self.settings = settings.validated()
        self.reset()

    def reset(self) -> None:
        self._still_since_ms = None
        self._pulse = None
        self._first = None
        self._settled_since_ms = None
        self._settled_after_first = False
        self._cooldown_until_ms = 0

    @staticmethod
    def _magnitudes(frame: RawMotionFrame) -> tuple[float, float]:
        accel = math.sqrt(
            frame.accel_x_g**2 + frame.accel_y_g**2 + frame.accel_z_g**2
        )
        gyro = math.sqrt(
            frame.gyro_x_dps**2 + frame.gyro_y_dps**2 + frame.gyro_z_dps**2
        )
        return abs(accel - 1.0), gyro

    def feed_batch(self, batch: RawMotionBatch) -> DoubleTapEvent | None:
        detected: DoubleTapEvent | None = None
        for frame in batch.frames:
            event = self._feed_frame(frame)
            if event is not None:
                detected = event
        return detected

    def _feed_frame(self, frame: RawMotionFrame) -> DoubleTapEvent | None:
        now = frame.timestamp_ms
        self._latest_ms = now
        dynamic, gyro = self._magnitudes(frame)
        settings = self.settings
        is_still = dynamic <= self.STILL_DYNAMIC_G and gyro <= self.STILL_GYRO_DPS

        if (self._first is not None and self._pulse is None
                and now - self._first.peak_at_ms > settings.max_interval_ms):
            self._first = None
            self._settled_since_ms = None
            self._settled_after_first = False

        if self._pulse is not None:
            if dynamic > self._pulse.peak_dynamic_accel_g:
                self._pulse.peak_dynamic_accel_g = dynamic
                self._pulse.peak_at_ms = now
            self._pulse.peak_gyro_dps = max(self._pulse.peak_gyro_dps, gyro)
            release_level = settings.impact_dynamic_accel_g * 0.55
            if dynamic <= release_level:
                pulse = self._pulse
                self._pulse = None
                if now - pulse.started_at_ms <= settings.max_pulse_ms:
                    event = self._finish_pulse(pulse)
                    if event is not None:
                        self._still_since_ms = now if is_still else None
                        return event
            elif now - self._pulse.started_at_ms > settings.max_pulse_ms:
                self._pulse = None
                self._first = None
                self._settled_after_first = False
            self._still_since_ms = now if is_still else None
            return None

        if self._first is not None:
            if is_still:
                self._settled_since_ms = self._settled_since_ms or now
                if now - self._settled_since_ms >= settings.settle_ms:
                    self._settled_after_first = True
            elif dynamic < settings.impact_dynamic_accel_g:
                self._settled_since_ms = None

        if (
            now >= self._cooldown_until_ms
            and dynamic >= settings.impact_dynamic_accel_g
            and gyro <= settings.max_tap_gyro_dps
        ):
            pre_still = 0 if self._still_since_ms is None else now - self._still_since_ms
            self._pulse = _TapPulse(now, pre_still, dynamic, gyro, now)
            self._still_since_ms = None
            return None

        if is_still:
            self._still_since_ms = self._still_since_ms or now
        elif self._first is None:
            self._still_since_ms = None
        return None

    def _finish_pulse(self, pulse: _TapPulse) -> DoubleTapEvent | None:
        settings = self.settings
        if pulse.peak_gyro_dps > settings.max_tap_gyro_dps:
            self._first = None
            return None
        if self._first is None:
            if pulse.pre_still_ms < settings.pre_still_ms:
                return None
            self._first = pulse
            self._settled_since_ms = None
            self._settled_after_first = False
            return None

        interval = pulse.peak_at_ms - self._first.peak_at_ms
        if not (
            settings.min_interval_ms <= interval <= settings.max_interval_ms
            and self._settled_after_first
        ):
            return None
        first = self._first
        self._first = None
        self._settled_since_ms = None
        self._settled_after_first = False
        self._cooldown_until_ms = pulse.started_at_ms + self.COOLDOWN_MS
        return DoubleTapEvent(
            first.peak_dynamic_accel_g,
            pulse.peak_dynamic_accel_g,
            max(first.peak_gyro_dps, pulse.peak_gyro_dps),
            interval,
            first.peak_at_ms,
            pulse.peak_at_ms,
        )


def extract_manual_double_tap(
    frames: tuple[RawMotionFrame, ...], *, after_timestamp_ms: int = -1
) -> DoubleTapEvent:
    """Extract the latest intended double tap without using learned thresholds."""
    eligible = [frame for frame in frames if frame.timestamp_ms > after_timestamp_ms]
    if len(eligible) < 12:
        raise ValueError("最近原始数据不足，请双敲后立即点击录入")
    cutoff = eligible[-1].timestamp_ms - MANUAL_CAPTURE_WINDOW_MS
    eligible = [frame for frame in eligible if frame.timestamp_ms >= cutoff]
    metrics = [DoubleTapDetector._magnitudes(frame) for frame in eligible]
    dynamics = [dynamic for dynamic, _gyro in metrics]
    lower_half = sorted(dynamics)[: max(5, len(dynamics) // 2)]
    baseline = statistics.median(lower_half)
    deviation = statistics.median(abs(value - baseline) for value in lower_half)
    threshold = max(
        MANUAL_CAPTURE_MIN_IMPACT_G,
        baseline + max(0.12, deviation * 6.0),
    )

    local_peaks: list[tuple[int, float, float]] = []
    for index, (dynamic, gyro) in enumerate(metrics):
        if dynamic < threshold:
            continue
        previous = metrics[index - 1][0] if index > 0 else -math.inf
        following = metrics[index + 1][0] if index + 1 < len(metrics) else -math.inf
        if dynamic >= previous and dynamic >= following:
            local_peaks.append((eligible[index].timestamp_ms, dynamic, gyro))

    # One table impact often spans several samples. Keep only its strongest local peak.
    selected: list[tuple[int, float, float]] = []
    for peak in sorted(local_peaks, key=lambda item: item[1], reverse=True):
        if all(abs(peak[0] - existing[0]) >= MANUAL_CAPTURE_PEAK_GAP_MS for existing in selected):
            selected.append(peak)
    selected.sort(key=lambda item: item[0])

    pairs: list[tuple[tuple[float, float, int], tuple[int, float, float], tuple[int, float, float]]] = []
    for first_index, first in enumerate(selected):
        for second in selected[first_index + 1 :]:
            interval = second[0] - first[0]
            if interval < MANUAL_CAPTURE_MIN_INTERVAL_MS:
                continue
            if interval > MANUAL_CAPTURE_MAX_INTERVAL_MS:
                break
            between = [
                metrics[index][0]
                for index, frame in enumerate(eligible)
                if first[0] < frame.timestamp_ms < second[0]
            ]
            if not between or min(between) > max(threshold * 0.90, min(first[1], second[1]) * 0.72):
                continue
            score = (min(first[1], second[1]), first[1] + second[1], second[0])
            pairs.append((score, first, second))
    if not pairs:
        raise ValueError("没有找到两次分开的桌面冲击，本次未计数")

    _score, first, second = max(pairs, key=lambda item: item[0])
    return DoubleTapEvent(
        first_peak_dynamic_accel_g=first[1],
        second_peak_dynamic_accel_g=second[1],
        peak_gyro_dps=max(first[2], second[2]),
        interval_ms=second[0] - first[0],
        first_at_ms=first[0],
        second_at_ms=second[0],
    )


@dataclass(frozen=True, slots=True)
class DoubleTapTemplate:
    features: tuple[tuple[float, float, float], ...]
    first_peak_dynamic_accel_g: float
    second_peak_dynamic_accel_g: float
    peak_gyro_dps: float
    interval_ms: int


@dataclass(frozen=True, slots=True)
class DoubleTapProfile:
    templates: tuple[DoubleTapTemplate, ...]
    acceptance_distance: float
    candidate_impact_g: float
    candidate_min_interval_ms: int
    candidate_max_interval_ms: int

    @property
    def trained(self) -> bool:
        return len(self.templates) >= 3


def default_double_tap_profile_path() -> Path:
    return user_data_dir() / "double-tap-profile-v2.json"


def extract_double_tap_template(
    frames: tuple[RawMotionFrame, ...], event: DoubleTapEvent
) -> DoubleTapTemplate:
    if event.second_at_ms <= event.first_at_ms:
        raise ValueError("双敲时间戳无效")
    start = event.first_at_ms - 180
    end = event.second_at_ms + 70
    selected = tuple(
        frame for frame in frames if start <= frame.timestamp_ms <= end
    )
    if len(selected) < 24:
        raise ValueError("双敲前后原始轨迹不足，请动作结束后立即录入")
    return DoubleTapTemplate(
        features=build_motion_features(selected),
        first_peak_dynamic_accel_g=event.first_peak_dynamic_accel_g,
        second_peak_dynamic_accel_g=event.second_peak_dynamic_accel_g,
        peak_gyro_dps=event.peak_gyro_dps,
        interval_ms=event.interval_ms,
    )


def double_tap_distance(first: DoubleTapTemplate, second: DoubleTapTemplate) -> float:
    trajectory = dtw_distance(first.features, second.features)
    interval_scale = max(120.0, (first.interval_ms + second.interval_ms) / 2.0)
    interval_penalty = abs(first.interval_ms - second.interval_ms) / interval_scale * 0.10
    first_balance = math.log(
        (first.first_peak_dynamic_accel_g + 0.20)
        / (first.second_peak_dynamic_accel_g + 0.20)
    )
    second_balance = math.log(
        (second.first_peak_dynamic_accel_g + 0.20)
        / (second.second_peak_dynamic_accel_g + 0.20)
    )
    balance_penalty = abs(first_balance - second_balance) * 0.025
    return trajectory + interval_penalty + balance_penalty


def train_double_tap_profile(
    templates: tuple[DoubleTapTemplate, ...],
) -> DoubleTapProfile:
    if len(templates) < 3:
        raise ValueError("至少需要 3 次双敲轨迹")
    nearest = [
        min(
            double_tap_distance(template, other)
            for other_index, other in enumerate(templates)
            if other_index != index
        )
        for index, template in enumerate(templates)
    ]
    ordered = sorted(nearest)
    percentile = ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.9))]
    minimum_peaks = [
        min(item.first_peak_dynamic_accel_g, item.second_peak_dynamic_accel_g)
        for item in templates
    ]
    intervals = [item.interval_ms for item in templates]
    return DoubleTapProfile(
        templates=templates,
        acceptance_distance=max(0.025, percentile * 1.42),
        candidate_impact_g=round(
            min(1.50, max(0.25, min(minimum_peaks) * 0.32)), 3
        ),
        candidate_min_interval_ms=max(80, min(intervals) - 120),
        candidate_max_interval_ms=min(1200, max(intervals) + 180),
    )


def save_double_tap_profile(profile: DoubleTapProfile, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": DOUBLE_TAP_PROFILE_SCHEMA,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "acceptance_distance": profile.acceptance_distance,
                "candidate_impact_g": profile.candidate_impact_g,
                "candidate_min_interval_ms": profile.candidate_min_interval_ms,
                "candidate_max_interval_ms": profile.candidate_max_interval_ms,
                "templates": [asdict(item) for item in profile.templates],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_double_tap_profile(path: Path) -> DoubleTapProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("schema_version") != DOUBLE_TAP_PROFILE_SCHEMA
        ):
            return None
        templates = tuple(
            DoubleTapTemplate(
                features=tuple(
                    tuple(float(value) for value in row)
                    for row in item["features"]
                ),
                first_peak_dynamic_accel_g=float(item["first_peak_dynamic_accel_g"]),
                second_peak_dynamic_accel_g=float(item["second_peak_dynamic_accel_g"]),
                peak_gyro_dps=float(item["peak_gyro_dps"]),
                interval_ms=int(item["interval_ms"]),
            )
            for item in data["templates"]
        )
        profile = DoubleTapProfile(
            templates=templates,
            acceptance_distance=float(data["acceptance_distance"]),
            candidate_impact_g=float(data["candidate_impact_g"]),
            candidate_min_interval_ms=int(data["candidate_min_interval_ms"]),
            candidate_max_interval_ms=int(data["candidate_max_interval_ms"]),
        )
        if (
            not profile.trained
            or not math.isfinite(profile.acceptance_distance)
            or profile.acceptance_distance <= 0
            or any(len(template.features) < 2 for template in profile.templates)
            or not 0.25 <= profile.candidate_impact_g <= 2.0
            or not 80 <= profile.candidate_min_interval_ms <= 500
            or not profile.candidate_min_interval_ms + 50
            <= profile.candidate_max_interval_ms
            <= 1200
        ):
            return None
        return profile
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def decode_ima_adpcm_chunk(chunk: AudioChunk) -> bytes:
    if not 0 <= chunk.step_index < len(IMA_STEP_TABLE):
        raise ValueError("ADPCM step index is invalid")
    if not 1 <= chunk.sample_count <= len(chunk.payload) * 2 + 1:
        raise ValueError("ADPCM sample count is invalid")
    predictor = int(chunk.predictor)
    step_index = int(chunk.step_index)
    samples = [predictor]
    for packed in chunk.payload:
        for code in (packed & 0x0F, (packed >> 4) & 0x0F):
            if len(samples) >= chunk.sample_count:
                break
            step = IMA_STEP_TABLE[step_index]
            difference = step >> 3
            if code & 1:
                difference += step >> 2
            if code & 2:
                difference += step >> 1
            if code & 4:
                difference += step
            predictor += -difference if code & 8 else difference
            predictor = min(32767, max(-32768, predictor))
            step_index += IMA_INDEX_TABLE[code & 7]
            step_index = min(88, max(0, step_index))
            samples.append(predictor)
    output = bytearray()
    for sample in samples:
        output.extend(int(sample).to_bytes(2, "little", signed=True))
    return bytes(output)


class VoiceAudioAssembler:
    def __init__(self) -> None:
        self.start: AudioStart | None = None
        self.expected_sequence = 0
        self.samples = 0
        self.pcm = bytearray()
        self.error: str | None = None

    def begin(self, start: AudioStart) -> None:
        if start.sample_rate != 16000 or start.codec != "IMA_ADPCM4":
            raise ValueError("unsupported voice audio format")
        self.start = start
        self.expected_sequence = 0
        self.samples = 0
        self.pcm.clear()
        self.error = None

    def add(self, chunk: AudioChunk) -> bytes:
        if self.start is None or chunk.session != self.start.session:
            raise ValueError("audio chunk has no matching session")
        if chunk.sequence != self.expected_sequence:
            self.error = (
                f"录音数据包缺失：期望 {self.expected_sequence}，收到 {chunk.sequence}"
            )
        self.expected_sequence = chunk.sequence + 1
        decoded = decode_ima_adpcm_chunk(chunk)
        self.pcm.extend(decoded)
        self.samples += chunk.sample_count
        return decoded

    def finish(self, end: AudioEnd) -> tuple[int, bytes]:
        if self.start is None or end.session != self.start.session:
            raise ValueError("audio end has no matching session")
        sample_rate = self.start.sample_rate
        self.start = None
        if self.error:
            error = self.error
            self.error = None
            raise ValueError(error)
        if end.total_samples != self.samples:
            raise ValueError(
                f"录音样本不完整：期望 {end.total_samples}，收到 {self.samples}"
            )
        if end.reason not in {"SILENCE", "TIMEOUT"}:
            raise ValueError(f"录音未完成：{end.reason}")
        if self.samples < sample_rate // 2:
            raise ValueError("录音过短，未进行文字识别")
        return sample_rate, bytes(self.pcm)


def _asset_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "assets"
    return Path(__file__).resolve().parents[2] / "assets"


def default_voice_runtime_dir() -> Path:
    # whisper.cpp 1.8.1 still opens model/audio paths through a narrow Windows
    # API. A Chinese user profile can therefore crash the CLI before inference.
    # ProgramData is machine-local, normally ASCII, and contains no recordings
    # after each temporary transcription directory is closed.
    return voice_runtime_dir()


def _file_matches(path: Path, expected_size: int, expected_sha256: str) -> bool:
    if not path.is_file() or path.stat().st_size != expected_size:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest().lower() == expected_sha256.lower()


def sanitize_voice_transcript(text: str) -> str:
    """Keep spoken command text while rejecting common silence hallucinations."""
    value = " ".join(str(text).split()).strip()
    value = _NON_SPEECH_TAG_RE.sub(" ", value)
    value = " ".join(value.split()).strip()
    value = _SPEAKER_PREFIX_RE.sub("", value).strip()
    if not value:
        return ""

    # Whisper frequently appends a short-video outro after otherwise valid text.
    # Only strip it when punctuation separates it from preceding command content.
    without_outro = _TRAILING_OUTRO_RE.sub("", value).strip(" ，,。.!！?？；;")
    if without_outro:
        value = without_outro

    value = to_simplified_chinese(value)
    canonical = re.sub(r"[\s，,。.!！?？:：；;、\-—_]+", "", value).casefold()
    if not canonical:
        return ""
    if any(pattern.fullmatch(canonical) for pattern in _FULL_HALLUCINATION_PATTERNS):
        return ""
    for phrase in _REPEATED_HALLUCINATIONS:
        if len(canonical) >= len(phrase) and canonical == phrase * (
            len(canonical) // len(phrase)
        ):
            return ""
    return value


class WhisperCppTranscriber:
    def __init__(self, runtime_dir: Path | None = None) -> None:
        self.runtime_dir = runtime_dir or default_voice_runtime_dir()

    @property
    def model_path(self) -> Path:
        return self.runtime_dir / MODEL_NAME

    @property
    def executable_path(self) -> Path:
        override = os.environ.get("CODEX_WHIP_WHISPER_CLI")
        if override:
            return Path(override).expanduser()
        bundled = _asset_root() / "stt" / f"whispercpp-{WHISPER_VERSION}"
        if sys.platform == "darwin":
            local = bundled / "whisper-cli"
            if local.is_file():
                return local
            discovered = shutil.which("whisper-cli") or shutil.which("whisper-cpp")
            if discovered:
                return Path(discovered)
            return local
        return bundled / "whisper-cli.exe"

    @property
    def bundled_vad_model_path(self) -> Path:
        return self.executable_path.parent / VAD_MODEL_NAME

    @property
    def vad_model_path(self) -> Path:
        # Keep every model/audio path ASCII. whisper.cpp 1.8.1 can crash when a
        # VAD model is opened through its narrow Windows path API.
        return self.runtime_dir / VAD_MODEL_NAME

    @property
    def ready(self) -> bool:
        return (
            self.executable_path.is_file()
            and self.model_path.is_file()
            and self.model_path.stat().st_size == MODEL_SIZE
            and self.vad_model_path.is_file()
            and self.vad_model_path.stat().st_size == VAD_MODEL_SIZE
        )

    def _prepare_vad_model(self) -> None:
        source = self.bundled_vad_model_path
        if not _file_matches(source, VAD_MODEL_SIZE, VAD_MODEL_SHA256):
            raise OSError("程序包中的 VAD 模型缺失或校验失败")
        if _file_matches(self.vad_model_path, VAD_MODEL_SIZE, VAD_MODEL_SHA256):
            return
        temporary = self.vad_model_path.with_suffix(self.vad_model_path.suffix + ".copy")
        try:
            shutil.copyfile(source, temporary)
            if not _file_matches(temporary, VAD_MODEL_SIZE, VAD_MODEL_SHA256):
                raise OSError("VAD 模型复制后校验失败")
            temporary.replace(self.vad_model_path)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def prepare(self, progress: Callable[[int, int], None] | None = None) -> None:
        if not self.executable_path.is_file():
            raise FileNotFoundError("程序包中缺少 whisper.cpp 运行时")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._prepare_vad_model()
        if self.model_path.is_file() and self.model_path.stat().st_size == MODEL_SIZE:
            return
        temporary = self.model_path.with_suffix(self.model_path.suffix + ".download")
        request = urllib.request.Request(
            MODEL_URL, headers={"User-Agent": "CodexWhip/1.3"}
        )
        digest = hashlib.sha256()
        downloaded = 0
        try:
            with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    downloaded += len(block)
                    if progress:
                        progress(downloaded, MODEL_SIZE)
            if downloaded != MODEL_SIZE:
                raise OSError(f"模型下载不完整：{downloaded} / {MODEL_SIZE} bytes")
            if digest.hexdigest().lower() != MODEL_SHA256:
                raise OSError("语音模型 SHA-256 校验失败")
            temporary.replace(self.model_path)
        except Exception:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def transcribe(self, sample_rate: int, pcm: bytes, *, on_started=None) -> str:
        if not self.ready:
            raise RuntimeError("本地语音模型尚未准备完成")
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="codex-whip-voice-", dir=self.runtime_dir) as folder:
            folder_path = Path(folder)
            wav_path = folder_path / "recording.wav"
            output_stem = folder_path / "transcript"
            with wave.open(str(wav_path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                output.writeframes(pcm)
            creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            if on_started is not None:
                on_started()
            completed = subprocess.run(
                [
                    str(self.executable_path),
                    "-m", str(self.model_path),
                    "-f", str(wav_path),
                    "-l", "zh",
                    "-otxt",
                    "-of", str(output_stem),
                    "-nt",
                    "-np",
                    "--no-fallback",
                    "--suppress-nst",
                    "--no-speech-thold", "0.55",
                    "--vad",
                    "--vad-model", str(self.vad_model_path),
                    "--vad-threshold", "0.55",
                    "--vad-min-speech-duration-ms", "300",
                    "--vad-min-silence-duration-ms", "160",
                    "--vad-speech-pad-ms", "80",
                ],
                cwd=self.executable_path.parent,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                creationflags=creation_flags,
            )
            transcript_path = output_stem.with_suffix(".txt")
            if completed.returncode != 0 or not transcript_path.is_file():
                detail = completed.stdout.strip()[-500:]
                raise RuntimeError(f"本地语音识别失败：{detail or completed.returncode}")
            text = " ".join(transcript_path.read_text(encoding="utf-8").split()).strip()
            return sanitize_voice_transcript(text)


def to_simplified_chinese(text: str) -> str:
    if not text:
        return text
    if os.name == "nt":
        mapper = ctypes.windll.kernel32.LCMapStringEx
        required = mapper(
            "zh-CN", 0x02000000, text, len(text), None, 0, None, None, 0
        )
        if required <= 0:
            return text
        output = ctypes.create_unicode_buffer(required)
        written = mapper(
            "zh-CN", 0x02000000, text, len(text), output, required, None, None, 0
        )
        return output.value if written > 0 else text
    if sys.platform == "darwin":
        try:
            from opencc import OpenCC

            return OpenCC("t2s").convert(text)
        except (ImportError, OSError, ValueError):
            return text
    return text


class VoiceModule:
    def __init__(
        self,
        store: VoiceSettingsStore,
        emit: Callable[[str, Any], None],
        transcriber: WhisperCppTranscriber | None = None,
        double_tap_profile_path: Path | None = None,
        virtual_microphone: Any | None = None,
    ) -> None:
        self.store = store
        self.emit = emit
        self.transcriber = transcriber or WhisperCppTranscriber()
        self._recording_transcriber = self.transcriber
        self._recording_gain = store.settings.recording_gain
        self.virtual_microphone = virtual_microphone
        self._recording_mode = store.settings.input_mode
        self.double_tap_profile_path = (
            double_tap_profile_path
            or store.path.with_name(default_double_tap_profile_path().name)
        )
        self.double_tap_profile = load_double_tap_profile(
            self.double_tap_profile_path
        )
        self.detector = DoubleTapDetector(store.settings)
        self.assembler = VoiceAudioAssembler()
        self._pending_text: str | None = None
        self._pending_until = 0.0
        self._pending_lock = threading.Lock()
        self._native_draft_pending = False
        self._calibration_remaining = 0
        self._calibration_events: list[DoubleTapEvent] = []
        self._calibration_templates: list[DoubleTapTemplate] = []
        self._calibration_after_ms = -1
        self._motion_frames: deque[RawMotionFrame] = deque()
        self._motion_lock = threading.Lock()
        self._force_lock = threading.RLock()
        self._force_calibration = None
        # A single hard motion is only a *candidate* first tap.  It must not
        # mask a genuine whip reported by the firmware.  We block firmware
        # whip events only after a complete double tap has actually fired.
        self._device_whip_block_until = 0.0
        self._install_tap_detector()

    def _install_tap_detector(self) -> None:
        from .tap_calibration import ForceTapDetector
        cls = ForceTapDetector if self.store.settings.tap_force_calibrated else DoubleTapDetector
        self.detector = cls(self.store.settings)

    def start_force_calibration(self, interval_ms: int | None = None) -> None:
        from .tap_calibration import TapCalibration
        self.cancel_calibration()
        with self._force_lock:
            self._force_calibration = TapCalibration(self.store.settings, interval_ms)
            self.emit('tap_calibration_state', self._force_calibration.snapshot())

    def set_tap_interval(self, interval_ms: int) -> None:
        with self._force_lock:
            if self._force_calibration is not None:
                self._force_calibration.set_interval(interval_ms)
                self.emit('tap_calibration_state', self._force_calibration.snapshot(
                    f'双敲最大间隔已设为 {interval_ms/1000:.2f} 秒，请按当前时长继续敲击。'))

    def save_force_calibration(self) -> None:
        with self._force_lock:
            session = self._force_calibration
            if session is None or session.stage != 'test' or session.draft is None:
                raise ValueError('请先完成轻敲和重敲两轮校准')
            # Persist one settings file before switching the live detector.
            # Old trajectory templates remain on disk for backward compatibility.
            self.store.update(session.draft)
            self._force_calibration = None
            self._install_tap_detector()
            self.emit('tap_calibration_saved', self.store.settings)

    def pause_force_calibration(self) -> None:
        with self._force_lock:
            session = self._force_calibration
            if session is not None:
                session.detector.reset()
                if session.stage == 'test':
                    session.test_detector.reset()
                self.emit('tap_calibration_state', session.snapshot(
                    '连接已断开；已采集数据保留。连接恢复后继续敲击即可。'))

    @property
    def pending_text(self) -> str | None:
        self.expire_pending()
        with self._pending_lock:
            return self._pending_text

    def expire_pending(self) -> bool:
        with self._pending_lock:
            if not self._pending_text or time.monotonic() < self._pending_until:
                return False
            self._pending_text = None
            self._pending_until = 0.0
        self.emit('voice_pending', None)
        return True

    def clear_pending(self) -> None:
        with self._pending_lock:
            self._pending_text = None
            self._pending_until = 0.0
        self.emit("voice_pending", None)

    def set_pending(self, text: str) -> None:
        value = " ".join(str(text).split()).strip()
        if not value:
            self.clear_pending()
            return
        with self._pending_lock:
            self._pending_text = value
            self._pending_until = time.monotonic() + 10.0
        self.emit("voice_pending", value)

    def mark_sent(self, prompt: str) -> None:
        with self._pending_lock:
            if self._pending_text != prompt:
                return
            self._pending_text = None
            self._pending_until = 0.0
        self.emit("voice_pending", None)

    @property
    def native_draft_pending(self) -> bool:
        with self._pending_lock:
            return self._native_draft_pending

    def mark_native_draft_ready(self) -> None:
        with self._pending_lock:
            self._native_draft_pending = True
        self.emit("voice_native_pending", True)

    def clear_native_draft(self) -> None:
        with self._pending_lock:
            self._native_draft_pending = False
        self.emit("voice_native_pending", False)

    def update_settings(self) -> None:
        self._install_tap_detector()
        if not self.store.settings.enabled:
            self.cancel_calibration()
            self.clear_pending()
            self.clear_native_draft()

    @property
    def double_tap_template_ready(self) -> bool:
        return bool(self.double_tap_profile and self.double_tap_profile.trained)

    def start_calibration(self, count: int = 5) -> None:
        total = max(1, int(count))
        with self._motion_lock:
            self._calibration_remaining = total
            self._calibration_events = []
            self._calibration_templates = []
            self._calibration_after_ms = (
                self._motion_frames[-1].timestamp_ms if self._motion_frames else -1
            )
        self.detector.reset()
        self.emit("voice_calibration", {"done": 0, "total": total})

    @property
    def calibration_active(self) -> bool:
        if self._force_calibration is not None:
            return True
        with self._motion_lock:
            return self._calibration_remaining > 0

    @property
    def suppress_whip(self) -> bool:
        return self.calibration_active or self.detector.suppress_whip

    @property
    def blocks_device_whip(self) -> bool:
        """Whether a completed voice gesture should own the current motion."""
        return self.calibration_active or time.monotonic() < self._device_whip_block_until

    def cancel_calibration(self) -> None:
        with self._force_lock:
            self._force_calibration = None
        with self._motion_lock:
            self._calibration_remaining = 0
            self._calibration_events = []
            self._calibration_templates = []
            self._calibration_after_ms = -1
        self.detector.reset()

    def record_calibration_sample(self) -> DoubleTapEvent:
        with self._motion_lock:
            total = self._calibration_remaining
            frames = tuple(self._motion_frames)
            after_timestamp_ms = self._calibration_after_ms
        if total <= 0:
            raise ValueError("请先点击“开始重新学习”")

        event = extract_manual_double_tap(
            frames, after_timestamp_ms=after_timestamp_ms
        )
        template = extract_double_tap_template(frames, event)
        with self._motion_lock:
            if self._calibration_remaining <= 0:
                raise ValueError("双敲学习已经结束")
            if event.second_at_ms <= self._calibration_after_ms:
                raise ValueError("没有新的双敲动作，本次未计数")
            self._calibration_events.append(event)
            self._calibration_templates.append(template)
            self._calibration_after_ms = event.second_at_ms
            events = tuple(self._calibration_events)
            templates = tuple(self._calibration_templates)
            done = len(events)
            total = self._calibration_remaining
        self.emit("voice_calibration", {"done": done, "total": total, "event": event})
        if done < total:
            return event

        settings = self.store.settings
        double_tap_profile = train_double_tap_profile(templates)
        learned = replace(
            settings,
            impact_dynamic_accel_g=double_tap_profile.candidate_impact_g,
            max_tap_gyro_dps=2000.0,
            min_interval_ms=double_tap_profile.candidate_min_interval_ms,
            max_interval_ms=double_tap_profile.candidate_max_interval_ms,
            pre_still_ms=min(settings.pre_still_ms, 120),
            settle_ms=20,
            max_pulse_ms=250,
        ).validated()
        save_double_tap_profile(
            double_tap_profile, self.double_tap_profile_path
        )
        self.store.update(learned)
        self.double_tap_profile = double_tap_profile
        self.detector.update_settings(learned)
        with self._motion_lock:
            self._calibration_remaining = 0
            self._calibration_events = []
            self._calibration_templates = []
            self._calibration_after_ms = -1
        self.emit("voice_calibration_done", learned)
        return event

    def _remember_motion(self, batch: RawMotionBatch) -> None:
        with self._motion_lock:
            for frame in batch.frames:
                self._motion_frames.append(frame)
                cutoff = frame.timestamp_ms - MANUAL_CAPTURE_WINDOW_MS
                while (
                    self._motion_frames
                    and self._motion_frames[0].timestamp_ms < cutoff
                ):
                    self._motion_frames.popleft()

    def feed_motion(self, batch: RawMotionBatch) -> tuple[bool, bool]:
        self._remember_motion(batch)
        with self._force_lock:
            if self._force_calibration is not None:
                session = self._force_calibration
                for frame in batch.frames:
                    state = (session.feed_test(frame) if session.stage == 'test'
                             else session.feed(frame))
                    if state is not None:
                        self.emit('tap_calibration_state', state)
                return False, True
        settings = self.store.settings
        if not settings.enabled and not self.calibration_active:
            return False, False
        if self.calibration_active:
            return False, True
        if self.detector.settings != settings:
            self.detector.update_settings(settings)
        event = self.detector.feed_batch(batch)
        if event is None:
            return False, self.detector.suppress_whip
        profile = self.double_tap_profile
        if profile is not None and profile.trained and not settings.tap_force_calibrated:
            with self._motion_lock:
                frames = tuple(self._motion_frames)
            try:
                candidate = extract_double_tap_template(frames, event)
            except ValueError as exc:
                self.emit(
                    "voice_match",
                    {"accepted": False, "detail": str(exc)},
                )
                return False, True
            score = min(
                double_tap_distance(candidate, template)
                for template in profile.templates
            )
            accepted = score <= profile.acceptance_distance
            self.emit(
                "voice_match",
                {
                    "accepted": accepted,
                    "score": score,
                    "threshold": profile.acceptance_distance,
                },
            )
            if not accepted:
                return False, True
        # Cover the tail of the accepted second impact.  Unlike
        # ``detector.suppress_whip``, this never activates for a lone first
        # impact, which may actually be the acceleration peak of a whip.
        self._device_whip_block_until = time.monotonic() + max(
            0.35, min(1.20, settings.settle_ms / 1000.0 + 0.30)
        )
        self.emit("voice_trigger", event)
        return True, True

    async def handle_audio(self, message: AudioStart | AudioChunk | AudioEnd) -> None:
        if isinstance(message, AudioStart):
            from .cloud_speech import SpeechRouter
            current = self.store.settings
            self._recording_gain = current.recording_gain
            self._recording_mode = current.input_mode
            self._recording_transcriber = (self.transcriber.snapshot()
                if isinstance(self.transcriber, SpeechRouter) else self.transcriber)
            replacing = self.pending_text is not None
            if replacing:
                # An accepted second double-tap means “discard and try again”.
                # Clear only after the device confirms a new recording session,
                # so a failed BLE start cannot silently destroy the old draft.
                self.clear_pending()
            self.assembler.begin(message)
            self.emit(
                "voice_state",
                {
                    "state": "recording",
                    "session": message.session,
                    "replacing": replacing,
                },
            )
            return
        if isinstance(message, AudioChunk):
            try:
                decoded = self.assembler.add(message)
                if self._recording_mode == "virtual_microphone":
                    if self.virtual_microphone is None:
                        raise ValueError("虚拟麦克风尚未初始化")
                    self.virtual_microphone.write(decoded)
            except Exception as exc:
                if self._recording_mode == "virtual_microphone" and self.virtual_microphone is not None:
                    self.virtual_microphone.abort()
                self.emit("voice_error", str(exc))
            return
        try:
            sample_rate, pcm = self.assembler.finish(message)
            from .voice_gain import apply_recording_gain
            pcm = await asyncio.to_thread(apply_recording_gain, pcm, self._recording_gain)
            from .voice_replay import recording_path, save_recording
            try:
                await asyncio.to_thread(save_recording, recording_path(self.store.path), sample_rate, pcm)
            except (OSError, ValueError) as exc:
                # Playback storage must not prevent transcription or sending.
                self.emit('log', f'上次录音保存失败，识别继续：{exc}')
            if self._recording_mode == "virtual_microphone":
                if self.virtual_microphone is None:
                    raise ValueError("虚拟麦克风尚未初始化")
                await asyncio.to_thread(self.virtual_microphone.finish)
                self.clear_pending()
                self.emit("voice_state", {"state": "dictation_ready", "session": message.session})
                return
            from threading import Event
            from .cloud_speech import SpeechRouter
            started = Event()
            transcriber = self._recording_transcriber
            def transcribe():
                if isinstance(transcriber, (SpeechRouter, WhisperCppTranscriber)):
                    return transcriber.transcribe(sample_rate, pcm, on_started=started.set)
                started.set()
                return transcriber.transcribe(sample_rate, pcm)
            task = asyncio.create_task(asyncio.to_thread(transcribe))
            announced = False
            try:
                # Do not flash a loading shape for immediate rejection/empty results.
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=.2)
                    if not done and started.is_set() and not announced:
                        self.emit("voice_state", {"state": "recognizing", "session": message.session})
                        announced = True
                text = task.result()
            finally:
                if not task.done():
                    task.cancel()
        except Exception as exc:
            if self._recording_mode == "virtual_microphone" and self.virtual_microphone is not None:
                self.virtual_microphone.abort()
            self.emit("voice_error", str(exc))
            return
        if not text:
            self.clear_pending()
            self.emit("voice_state", {"state": "empty", "session": message.session})
            return
        self.set_pending(text)
        self.emit("voice_state", {"state": "ready", "text": text})
