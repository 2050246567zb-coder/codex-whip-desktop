"""Route the handle's 16 kHz mono audio to a known virtual microphone device."""
from __future__ import annotations

from array import array
from dataclasses import dataclass
import queue
import sys
import threading
from typing import Any, Callable


class VirtualMicrophoneError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VirtualMicrophoneDevice:
    index: int
    name: str
    hostapi: int
    sample_rate: float


def _device_hints(platform: str) -> tuple[str, ...]:
    if platform == "darwin":
        return ("codex whip", "blackhole 2ch", "blackhole")
    return ("codex whip", "virtual audio driver", "cable input", "vb-audio virtual cable")


def select_virtual_output(devices: Any, *, platform: str | None = None) -> VirtualMicrophoneDevice:
    """Select only an explicitly recognised loopback output; never leak to speakers."""
    hints = _device_hints(platform or sys.platform)
    matches: list[tuple[int, VirtualMicrophoneDevice]] = []
    for index, raw in enumerate(devices):
        try:
            name = str(raw["name"])
            channels = int(raw["max_output_channels"])
        except (KeyError, TypeError, ValueError):
            continue
        if channels < 1:
            continue
        folded = name.casefold()
        scores = [len(hints) - position for position, hint in enumerate(hints) if hint in folded]
        if not scores:
            continue
        matches.append((max(scores), VirtualMicrophoneDevice(
            index=index,
            name=name,
            hostapi=int(raw.get("hostapi", -1)),
            sample_rate=float(raw.get("default_samplerate", 48000.0)),
        )))
    if not matches:
        expected = "BlackHole 2ch" if (platform or sys.platform) == "darwin" else "Virtual Audio Driver 或 CABLE Input"
        raise VirtualMicrophoneError(f"未找到虚拟麦克风设备（需要 {expected}）")
    matches.sort(key=lambda item: item[0], reverse=True)
    return matches[0][1]


def _pcm16_samples(pcm: bytes) -> array:
    if len(pcm) % 2:
        raise VirtualMicrophoneError("收到不完整的 16 位录音数据")
    values = array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    return values


def upsample_16k_to_48k(pcm: bytes, previous: int | None = None) -> tuple[bytes, int | None]:
    """Stateful linear 3x resampling with an exact 3:1 output length."""
    samples = _pcm16_samples(pcm)
    if not samples:
        return b"", previous
    output = array("h")
    prior = int(samples[0]) if previous is None else int(previous)
    for sample in samples:
        current = int(sample)
        delta = current - prior
        output.extend((prior + round(delta / 3), prior + round(delta * 2 / 3), current))
        prior = current
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes(), prior


def amplify_pcm16(pcm: bytes, gain: float) -> bytes:
    values = _pcm16_samples(pcm)
    output = array("h", (max(-32768, min(32767, round(value * gain))) for value in values))
    if sys.byteorder != "little":
        output.byteswap()
    return output.tobytes()


class VirtualMicrophoneBridge:
    """Bounded background writer for BlackHole/VAD/VB-CABLE output endpoints."""

    def __init__(self, *, backend_factory: Callable[[], Any] | None = None,
                 platform: str | None = None) -> None:
        self._backend_factory = backend_factory or self._sounddevice
        self._platform = platform or sys.platform
        self._backend: Any | None = None
        self._stream: Any | None = None
        self._queue: queue.Queue[bytes | None] | None = None
        self._thread: threading.Thread | None = None
        self._error: Exception | None = None
        self._gain = 1.0
        self._previous: int | None = None

    @staticmethod
    def _sounddevice() -> Any:
        try:
            import sounddevice
        except ImportError as exc:
            raise VirtualMicrophoneError("产品版缺少虚拟麦克风音频组件") from exc
        return sounddevice

    @property
    def active(self) -> bool:
        return self._stream is not None

    def detect(self) -> VirtualMicrophoneDevice:
        backend = self._backend_factory()
        return select_virtual_output(backend.query_devices(), platform=self._platform)

    def start(self, gain: float = 1.0) -> VirtualMicrophoneDevice:
        self.abort()
        backend = self._backend_factory()
        device = select_virtual_output(backend.query_devices(), platform=self._platform)
        try:
            stream = backend.RawOutputStream(
                device=device.index, samplerate=48000, channels=1,
                dtype="int16", blocksize=960, latency="low",
            )
            stream.start()
        except Exception as exc:
            raise VirtualMicrophoneError(f"无法打开虚拟麦克风 {device.name}：{exc}") from exc
        self._backend = backend
        self._stream = stream
        self._queue = queue.Queue(maxsize=160)
        self._error = None
        self._gain = max(1.0, min(8.0, float(gain)))
        self._previous = None
        self._thread = threading.Thread(target=self._run, name="codex-whip-virtual-mic", daemon=True)
        self._thread.start()
        # Give Codex a clean lead-in while its dictation session opens.
        self._put(bytes(4800 * 2))
        return device

    def _run(self) -> None:
        assert self._queue is not None and self._stream is not None
        try:
            while True:
                block = self._queue.get()
                if block is None:
                    return
                self._stream.write(block)
        except Exception as exc:
            self._error = exc

    def _put(self, block: bytes) -> None:
        if self._queue is None:
            raise VirtualMicrophoneError("虚拟麦克风尚未启动")
        if self._error is not None:
            raise VirtualMicrophoneError(f"虚拟麦克风写入失败：{self._error}")
        try:
            self._queue.put(block, timeout=0.5)
        except queue.Full as exc:
            raise VirtualMicrophoneError("虚拟麦克风缓冲已满，请检查音频设备") from exc

    def write(self, pcm_16khz: bytes) -> None:
        amplified = amplify_pcm16(pcm_16khz, self._gain)
        converted, self._previous = upsample_16k_to_48k(amplified, self._previous)
        if converted:
            self._put(converted)

    def finish(self) -> None:
        if self._stream is None:
            raise VirtualMicrophoneError("虚拟麦克风尚未启动")
        self._put(bytes(4800 * 2))
        assert self._queue is not None
        self._queue.put(None, timeout=0.5)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                self.abort()
                raise VirtualMicrophoneError("虚拟麦克风结束超时")
        error = self._error
        self._close_stream()
        if error is not None:
            raise VirtualMicrophoneError(f"虚拟麦克风写入失败：{error}")

    def _close_stream(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        self._queue = None
        self._thread = None
        self._backend = None
        self._previous = None

    def abort(self) -> None:
        if self._queue is not None:
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
        if self._thread is not None:
            self._thread.join(timeout=0.5)
        self._close_stream()
