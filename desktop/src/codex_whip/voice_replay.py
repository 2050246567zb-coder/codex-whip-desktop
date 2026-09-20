"""One local ASR-input recording and independently stoppable playback."""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
import wave


def recording_path(settings_path: Path) -> Path:
    return settings_path.with_name('last-voice-recording.wav')


def save_recording(path: Path, sample_rate: int, pcm: bytes) -> None:
    if sample_rate != 16000 or not pcm or len(pcm) % 2 or len(pcm) > 16000 * 2 * 30:
        raise ValueError('无效的录音格式')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.voice-', suffix='.wav', delete=False) as f:
            temporary = Path(f.name)
            with wave.open(f, 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def recording_duration(path: Path) -> float | None:
    try:
        with wave.open(str(path), 'rb') as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                return None
            duration = wav.getnframes() / wav.getframerate()
            return duration if 0 < duration <= 30 else None
    except (OSError, ValueError, EOFError, wave.Error):
        return None


class RecordingPlayer:
    def __init__(self):
        self._alias = 'whipvoice' + uuid.uuid4().hex
        self._opened = False
        self._process = None
        self._snapshot = None

    @staticmethod
    def _mci(command: str) -> str:
        dll = ctypes.WinDLL('winmm')
        send = dll.mciSendStringW
        send.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        send.restype = ctypes.c_uint
        buffer = ctypes.create_unicode_buffer(512)
        code = send(command, buffer, len(buffer), None)
        if code:
            raise OSError(f'录音播放失败（音频设备错误 {code}）')
        return buffer.value

    def play(self, path: Path) -> None:
        self.stop()
        if recording_duration(path) is None:
            raise ValueError('尚无可回放的识别录音')
        try:
            # A private snapshot lets a new ASR recording replace the last file
            # without locking it or changing audio midway through playback.
            with tempfile.NamedTemporaryFile(prefix='whip-replay-', suffix='.wav', delete=False) as f:
                self._snapshot = Path(f.name)
                f.write(path.read_bytes())
            if os.name == 'nt':
                self._mci(f'open "{self._snapshot}" type waveaudio alias {self._alias}')
                self._opened = True
                self._mci(f'play {self._alias}')
            elif sys.platform == 'darwin':
                self._process = subprocess.Popen(['/usr/bin/afplay', str(self._snapshot)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                raise OSError('当前系统暂不支持录音回放')
        except Exception:
            self.stop()
            raise

    @property
    def playing(self) -> bool:
        if self._opened:
            return self._mci(f'status {self._alias} mode') == 'playing'
        return self._process is not None and self._process.poll() is None

    def stop(self) -> None:
        if self._opened:
            self._mci(f'close {self._alias}')
            self._opened = False
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                self._process.wait(timeout=1)
            self._process = None
        if self._snapshot is not None:
            self._snapshot.unlink(missing_ok=True)
            self._snapshot = None
