"""Explicit opt-in ASR presets. No chat rewriting, retries or provider fallback."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
import socket
import struct
import sys
from types import SimpleNamespace
import urllib.error
import urllib.request
import uuid
import wave


@dataclass(frozen=True)
class Preset:
    label: str
    model: str
    url: str
    credential: str
    protocol: str = 'multipart'


PRESETS = {
    'openai-mini': Preset('OpenAI · GPT-4o mini Transcribe', 'gpt-4o-mini-transcribe',
        'https://api.openai.com/v1/audio/transcriptions', 'openai'),
    'openai': Preset('OpenAI · GPT-4o Transcribe', 'gpt-4o-transcribe',
        'https://api.openai.com/v1/audio/transcriptions', 'openai'),
    'qwen-cn': Preset('百炼 · Qwen3 ASR（北京）', 'qwen3-asr-flash',
        'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', 'qwen-cn', 'qwen'),
    'qwen-sg': Preset('百炼 · Qwen3 ASR（新加坡）', 'qwen3-asr-flash',
        'https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions', 'qwen-sg', 'qwen'),
    'siliconflow': Preset('硅基流动 · SenseVoice', 'FunAudioLLM/SenseVoiceSmall',
        'https://api.siliconflow.cn/v1/audio/transcriptions', 'siliconflow'),
}
LOCAL_LABEL = '本地识别 · 离线'


class SpeechError(RuntimeError):
    pass


class SpeechKeys:
    """Choose OS vaults explicitly; never fall back to a plaintext keyring."""
    SERVICE = 'CodexWhip.Speech'

    def _backend(self):
        try:
            if sys.platform == 'win32':
                from keyring.backends.Windows import WinVaultKeyring
                return WinVaultKeyring()
            if sys.platform == 'darwin':
                from keyring.backends.macOS import Keyring
                return Keyring()
        except Exception:
            raise SpeechError('系统密钥存储不可用，请重新安装完整版本') from None
        raise SpeechError('此系统暂不支持安全保存 API Key')

    def get(self, preset):
        try:
            return self._backend().get_password(self.SERVICE, PRESETS[preset].credential) or ''
        except Exception:
            raise SpeechError('无法读取系统密钥，请检查凭据存储权限') from None

    def set(self, preset, key):
        key = key.strip()
        if not key or len(key) > 2048 or any(not 33 <= ord(c) <= 126 for c in key):
            raise SpeechError('API Key 格式无效')
        try:
            self._backend().set_password(self.SERVICE, PRESETS[preset].credential, key)
        except Exception:
            raise SpeechError('无法保存到系统密钥存储；未写入普通设置文件') from None

    def delete(self, preset):
        try:
            if self.get(preset):
                self._backend().delete_password(self.SERVICE, PRESETS[preset].credential)
        except Exception:
            raise SpeechError('无法清除系统密钥') from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an API key or recording to a redirected host.
        return None


def request_body(preset: Preset, audio: bytes):
    if preset.protocol == 'qwen':
        body = {'model': preset.model, 'stream': False,
                'messages': [{'role': 'user', 'content': [{'type': 'input_audio',
                    'input_audio': {'data': 'data:audio/wav;base64,' + base64.b64encode(audio).decode('ascii')}}]}],
                'asr_options': {'enable_itn': False}}
        return json.dumps(body).encode(), 'application/json'
    boundary = 'Whip' + uuid.uuid4().hex
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="model"\r\n\r\n{preset.model}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording.wav"\r\n'
            'Content-Type: audio/wav\r\n\r\n').encode()
    return body + audio + f'\r\n--{boundary}--\r\n'.encode(), f'multipart/form-data; boundary={boundary}'


class SpeechRouter:
    def __init__(self, store, local, keys=None):
        self.store, self.local = store, local
        self.keys = keys or SpeechKeys()

    def snapshot(self):
        # A setting change while audio is being collected applies only to the
        # next recording, never silently rerouting an in-flight recording.
        return SpeechRouter(SimpleNamespace(settings=self.store.settings), self.local, self.keys)

    @property
    def ready(self):
        preset = self.store.settings.speech_provider
        if preset == 'local':
            return self.local.ready
        try:
            return bool(self.keys.get(preset))
        except SpeechError:
            return False

    def prepare(self, progress=None):
        if self.store.settings.speech_provider == 'local':
            return self.local.prepare(progress)
        if not self.ready:
            raise SpeechError('请在设置 → 输入中填写并保存 API Key')

    def transcribe(self, sample_rate, pcm, *, on_started=None):
        provider = self.store.settings.speech_provider
        if provider == 'local':
            if on_started is None:
                return self.local.transcribe(sample_rate, pcm)
            return self.local.transcribe(sample_rate, pcm, on_started=on_started)
        preset = PRESETS[provider]
        if sample_rate != 16000 or len(pcm) % 2 or len(pcm) > 16000*2*31:
            raise SpeechError('录音格式或长度无效，未上传')
        # Suppress digital silence before upload; acoustic silence/hallucinations
        # are additionally handled by the existing transcript sanitizer.
        if not pcm or max(abs(v[0]) for v in struct.iter_unpack('<h', pcm)) < 16:
            return ''
        key = self.keys.get(provider)
        if not key:
            raise SpeechError('请先保存 API Key')
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        body, content_type = request_body(preset, audio.getvalue())
        request = urllib.request.Request(preset.url, data=body, method='POST',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': content_type})
        try:
            if on_started is not None:
                on_started()
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(1024*1024+1)
            if len(raw) > 1024*1024:
                raise SpeechError('语音服务返回内容过大')
            data = json.loads(raw)
            text = data['choices'][0]['message']['content'] if preset.protocol == 'qwen' else data['text']
            if not isinstance(text, str):
                raise ValueError('invalid transcript')
        except urllib.error.HTTPError as exc:
            messages = {401: 'API Key 无效', 403: 'API Key 无权限或地区不匹配',
                        429: '服务限流或额度不足', 404: '模型暂不可用',
                        413: '录音超过服务限制'}
            raise SpeechError(messages.get(exc.code, f'语音服务请求失败（HTTP {exc.code}）')) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise SpeechError('语音服务连接失败或超时，请检查网络后重新录音') from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise SpeechError('语音服务返回格式异常') from None
        from .voice import sanitize_voice_transcript
        return sanitize_voice_transcript(text)
