"""Speech recognition: configured API first, private local fallback second."""
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
    'doubao': Preset('豆包 · 录音极速识别（API Key）', 'bigmodel',
        'https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash', 'doubao', 'doubao'),
    'doubao-legacy': Preset('豆包 · 录音极速识别（App ID + Token）', 'bigmodel',
        'https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash', 'doubao-legacy', 'doubao-legacy'),
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


def _valid_secret(value):
    return isinstance(value, str) and 0 < len(value) <= 2048 and all(33 <= ord(c) <= 126 for c in value)


def encode_doubao_credentials(app_id: str, token: str) -> str:
    app_id, token = app_id.strip(), token.strip()
    if not app_id.isascii() or not app_id.isdecimal() or len(app_id) > 64 or not _valid_secret(token):
        raise SpeechError('请填写数字 App ID 和有效的 Access Token')
    encoded = json.dumps({'app_id': app_id, 'token': token}, separators=(',', ':'))
    if len(encoded) > 2048:
        raise SpeechError('豆包凭据过长')
    return encoded


def decode_doubao_credentials(value: str) -> tuple[str, str]:
    try:
        data = json.loads(value)
        app_id, token = data['app_id'], data['token']
        encode_doubao_credentials(app_id, token)
        return app_id.strip(), token.strip()
    except (ValueError, KeyError, TypeError, AttributeError, SpeechError):
        raise SpeechError('请重新填写并保存豆包 App ID 和 Access Token') from None


def doubao_headers(provider: str, key: str) -> dict[str, str]:
    headers = {'X-Api-Resource-Id': 'volc.bigasr.auc_turbo',
               'X-Api-Request-Id': str(uuid.uuid4()), 'X-Api-Sequence': '-1'}
    if provider == 'doubao-legacy':
        app_id, token = decode_doubao_credentials(key)
        headers.update({'X-Api-App-Key': app_id, 'X-Api-Access-Key': token})
    else:
        if not _valid_secret(key):
            raise SpeechError('豆包语音 API Key 格式无效')
        headers['X-Api-Key'] = key
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
    if preset.protocol.startswith('doubao'):
        body = {'user': {'uid': 'codex-whip'},
                'audio': {'data': base64.b64encode(audio).decode('ascii')},
                'request': {'model_name': 'bigmodel', 'enable_itn': False, 'enable_ddc': False}}
        return json.dumps(body).encode(), 'application/json'
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
            key = self.keys.get(preset)
            if preset == 'doubao-legacy' and key:
                decode_doubao_credentials(key)
            return bool(key) or self.local.ready is True
        except SpeechError:
            return self.local.ready is True

    def prepare(self, progress=None):
        if self.store.settings.speech_provider == 'local' or not self._cloud_ready():
            return self.local.prepare(progress)

    def _cloud_ready(self):
        provider = self.store.settings.speech_provider
        if provider == 'local':
            return False
        try:
            key = self.keys.get(provider)
            if provider == 'doubao-legacy' and key:
                decode_doubao_credentials(key)
            return bool(key)
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
        if provider == 'local':
            return self._local_transcribe(sample_rate, pcm, on_started)
        if not self._cloud_ready():
            if self.local.ready is True:
                return self._local_transcribe(sample_rate, pcm, on_started)
            raise SpeechError('云端识别未配置，本地识别也尚未就绪')
        try:
            return self._transcribe_cloud(provider, sample_rate, pcm, on_started=on_started)
        except SpeechError:
            if self.local.ready is not True:
                raise
            # Cloud dispatch may already have emitted ``on_started``. Avoid a
            # duplicate recording-state transition while retrying locally.
            return self._local_transcribe(sample_rate, pcm, None)

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
        headers = doubao_headers(provider, key) if preset.protocol.startswith('doubao') else {'Authorization': f'Bearer {key}'}
        headers['Content-Type'] = content_type
        request = urllib.request.Request(preset.url, data=body, method='POST',
            headers=headers)
        try:
            if on_started is not None:
                on_started()
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as response:
                if preset.protocol.startswith('doubao') and not check_doubao_status(response.headers):
                    return ''
                raw = response.read(1024*1024+1)
            if len(raw) > 1024*1024:
                raise SpeechError('语音服务返回内容过大')
            data = json.loads(raw)
            if preset.protocol.startswith('doubao'):
                text = data['result']['text']
            else:
                text = data['choices'][0]['message']['content'] if preset.protocol == 'qwen' else data['text']
            if not isinstance(text, str):
                raise ValueError('invalid transcript')
        except urllib.error.HTTPError as exc:
            messages = {401: 'API Key 无效', 403: 'API Key 无权限或地区不匹配',
                        429: '服务限流或额度不足', 404: '模型暂不可用',
                        413: '录音超过服务限制'}
            if preset.protocol.startswith('doubao'):
                messages.update({401: '豆包语音凭据无效；请使用语音控制台的 Key 或 App ID + Token',
                                 403: '豆包语音无权限；请开通录音极速识别 volc.bigasr.auc_turbo'})
            raise SpeechError(messages.get(exc.code, f'语音服务请求失败（HTTP {exc.code}）')) from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise SpeechError('语音服务连接失败或超时，请检查网络后重新录音') from None
        except (ValueError, KeyError, IndexError, TypeError):
            raise SpeechError('语音服务返回格式异常') from None
        from .voice import sanitize_voice_transcript
        return sanitize_voice_transcript(text)
