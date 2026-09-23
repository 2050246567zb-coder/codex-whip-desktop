"""Speech recognition: configured API first, private local fallback second."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time
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
    query_url: str = ''
    resource_id: str = ''


PRESETS = {
    'doubao-v2': Preset('豆包 · 录音文件识别 2.0', 'bigmodel',
        'https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit',
        'doubao-v2', 'doubao-v2',
        'https://openspeech.bytedance.com/api/v3/auc/bigmodel/query',
        'volc.seedasr.auc'),
}
PRODUCT_PROVIDER = 'doubao-v2'
DOUBAO_QUERY_INTERVAL_SECONDS = 0.25
DOUBAO_QUERY_DEADLINE_SECONDS = 25.0


class SpeechError(RuntimeError):
    pass


def _valid_secret(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and all(33 <= ord(c) <= 126 for c in value)


def bundled_doubao_key_path() -> Path:
    root = (Path(sys._MEIPASS) if getattr(sys, 'frozen', False)
            else Path(__file__).resolve().parents[2])
    return root / 'assets' / 'private' / 'doubao-api-key.txt'


def bundled_doubao_key() -> str:
    """Return the deliberately bundled product key without logging it."""
    value = os.environ.get('CODEX_WHIP_DOUBAO_API_KEY', '').strip()
    if not value:
        try:
            value = bundled_doubao_key_path().read_text(encoding='utf-8').strip()
        except OSError:
            return ''
    return value if _valid_secret(value) else ''


def doubao_headers(provider: str, key: str, *, request_id: str | None = None) -> dict[str, str]:
    preset = PRESETS[provider]
    if not _valid_secret(key):
        raise SpeechError('豆包语音 API Key 格式无效')
    headers = {'X-Api-Resource-Id': preset.resource_id,
               'X-Api-Request-Id': request_id or str(uuid.uuid4()),
               'X-Api-Sequence': '-1', 'X-Api-Key': key}
    return headers


def check_doubao_status(headers) -> bool:
    """Return False for silence; never echo untrusted service messages/secrets."""
    code = headers.get('X-Api-Status-Code', '')
    if code == '20000003':
        return False
    if code == '20000000':
        return True
    messages = {'45000001': '豆包请求参数无效', '45000002': '豆包未收到有效音频',
                '45000151': '豆包不支持此音频格式', '55000031': '豆包服务繁忙，请稍后重新录音'}
    raise SpeechError(messages.get(code, '豆包识别未成功，请检查语音服务权限与额度'))


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
        if preset == PRODUCT_PROVIDER:
            bundled = bundled_doubao_key()
            if bundled:
                return bundled
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
    body = {'user': {'uid': 'codex-whip'},
            'audio': {'data': base64.b64encode(audio).decode('ascii'),
                      'format': 'wav', 'language': 'zh-CN'},
            'request': {'model_name': preset.model, 'enable_itn': True,
                        'enable_punc': True, 'enable_ddc': False}}
    return json.dumps(body).encode(), 'application/json'


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
        if not self.store.settings.precise_recognition:
            return self.local.ready is True
        preset = self.store.settings.speech_provider
        try:
            key = self.keys.get(preset)
            return bool(key) or self.local.ready is True
        except SpeechError:
            return self.local.ready is True

    def prepare(self, progress=None):
        if not self._cloud_ready():
            return self.local.prepare(progress)

    def _cloud_ready(self):
        if not self.store.settings.precise_recognition:
            return False
        provider = self.store.settings.speech_provider
        try:
            return bool(self.keys.get(provider))
        except SpeechError:
            return False

    def _local_transcribe(self, sample_rate, pcm, on_started):
        if not self.local.ready:
            self.local.prepare()
        if on_started is None:
            return self.local.transcribe(sample_rate, pcm)
        return self.local.transcribe(sample_rate, pcm, on_started=on_started)

    def transcribe(self, sample_rate, pcm, *, on_started=None):
        provider = self.store.settings.speech_provider
        if not self._cloud_ready():
            return self._local_transcribe(sample_rate, pcm, on_started)
        try:
            return self._transcribe_cloud(provider, sample_rate, pcm, on_started=on_started)
        except SpeechError as cloud_error:
            # Cloud dispatch may already have emitted ``on_started``. Avoid a
            # duplicate recording-state transition while preparing/retrying
            # locally. A first-run local model may need preparation here.
            try:
                return self._local_transcribe(sample_rate, pcm, None)
            except Exception:
                raise cloud_error from None

    def _transcribe_cloud(self, provider, sample_rate, pcm, *, on_started=None):
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
        request_id = str(uuid.uuid4())
        headers = doubao_headers(provider, key, request_id=request_id)
        headers['Content-Type'] = content_type
        request = urllib.request.Request(preset.url, data=body, method='POST',
            headers=headers)
        try:
            if on_started is not None:
                on_started()
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=30) as response:
                if not check_doubao_status(response.headers):
                    return ''
                raw = response.read(1024*1024+1)
            if preset.protocol == 'doubao-v2':
                deadline = time.monotonic() + DOUBAO_QUERY_DEADLINE_SECONDS
                while True:
                    query_headers = doubao_headers(provider, key, request_id=request_id)
                    query_headers['Content-Type'] = 'application/json'
                    query = urllib.request.Request(
                        preset.query_url, data=b'{}', method='POST', headers=query_headers)
                    with opener.open(query, timeout=10) as response:
                        code = response.headers.get('X-Api-Status-Code', '')
                        raw = response.read(1024*1024+1)
                    if code == '20000000':
                        break
                    if code == '20000003':
                        return ''
                    if code not in {'20000001', '20000002'}:
                        check_doubao_status({'X-Api-Status-Code': code})
                    if time.monotonic() >= deadline:
                        raise SpeechError('豆包识别等待超时，已切换本地识别')
                    time.sleep(DOUBAO_QUERY_INTERVAL_SECONDS)
            if len(raw) > 1024*1024:
                raise SpeechError('语音服务返回内容过大')
            data = json.loads(raw)
            text = data['result']['text']
            if not isinstance(text, str):
                raise ValueError('invalid transcript')
        except urllib.error.HTTPError as exc:
            messages = {401: 'API Key 无效', 403: 'API Key 无权限或地区不匹配',
                        429: '服务限流或额度不足', 404: '模型暂不可用',
                        413: '录音超过服务限制'}
            messages.update({401: '豆包语音凭据无效',
                             403: '豆包语音无权限；请开通录音文件识别 2.0（volc.seedasr.auc）'})
            raise SpeechError(messages.get(exc.code, f'语音服务请求失败（HTTP {exc.code}）')) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise SpeechError('语音服务连接失败或超时，请检查网络后重新录音') from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise SpeechError('语音服务返回格式异常') from None
        from .voice import sanitize_voice_transcript
        return sanitize_voice_transcript(text)
