import base64
import io
import json
import struct
import urllib.error
import wave
from unittest.mock import Mock

import pytest

from codex_whip.cloud_speech import (
    PRESETS,
    PRODUCT_PROVIDER,
    SpeechError,
    SpeechKeys,
    SpeechRouter,
    _NoRedirect,
    bundled_doubao_key,
)
from codex_whip.voice import VoiceSettings, VoiceSettingsStore


def _response(body: dict, status: str):
    response = io.BytesIO(json.dumps(body).encode())
    response.headers = {'X-Api-Status-Code': status}
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    return context


@pytest.fixture
def setup(tmp_path):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    local = Mock()
    local.ready = False
    keys = Mock()
    keys.get.return_value = 'TEST-KEY-DO-NOT-LOG'
    return store, local, keys


def test_product_has_only_doubao_recording_v2():
    assert tuple(PRESETS) == (PRODUCT_PROVIDER,)
    preset = PRESETS[PRODUCT_PROVIDER]
    assert preset.resource_id == 'volc.seedasr.auc'
    assert preset.url.endswith('/api/v3/auc/bigmodel/submit')
    assert preset.query_url.endswith('/api/v3/auc/bigmodel/query')
    assert VoiceSettings().speech_provider == PRODUCT_PROVIDER
    assert VoiceSettings().precise_recognition is True


def test_local_only_switch_never_reads_key_or_uploads(setup, monkeypatch):
    store, local, keys = setup
    store.update(VoiceSettings(enabled=True, precise_recognition=False))
    local.transcribe.return_value = '本地识别结果'
    keys.get.side_effect = AssertionError('local mode must not read the cloud key')
    network = Mock(side_effect=AssertionError('local mode must not upload audio'))
    monkeypatch.setattr('urllib.request.build_opener', network)

    router = SpeechRouter(store, local, keys)
    assert router.ready is False
    router.prepare()
    local.prepare.assert_called_once()
    assert router.transcribe(16000, b'\0\0') == '本地识别结果'
    keys.get.assert_not_called()
    network.assert_not_called()


def test_recognition_mode_is_snapshotted_at_recording_start(setup, monkeypatch):
    store, local, keys = setup
    store.update(VoiceSettings(enabled=True, precise_recognition=False))
    local.transcribe.return_value = '本地识别结果'
    router = SpeechRouter(store, local, keys).snapshot()
    store.update(VoiceSettings(enabled=True, precise_recognition=True))
    network = Mock(side_effect=AssertionError('current recording must remain local'))
    monkeypatch.setattr('urllib.request.build_opener', network)

    assert router.transcribe(16000, b'\0\0') == '本地识别结果'
    keys.get.assert_not_called()
    network.assert_not_called()


def test_doubao_v2_submits_then_queries_same_request(setup, monkeypatch):
    store, local, keys = setup
    opener = Mock()
    opener.open.side_effect = [
        _response({}, '20000000'),
        _response({'result': {'text': '继续完成任务'}}, '20000000'),
    ]
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    pcm = struct.pack('<h', 800) * 16000

    assert SpeechRouter(store, local, keys).transcribe(16000, pcm) == '继续完成任务'
    submit, query = [call.args[0] for call in opener.open.call_args_list]
    assert submit.full_url == PRESETS[PRODUCT_PROVIDER].url
    assert query.full_url == PRESETS[PRODUCT_PROVIDER].query_url
    submit_headers = {key.lower(): value for key, value in submit.headers.items()}
    query_headers = {key.lower(): value for key, value in query.headers.items()}
    assert submit_headers['x-api-resource-id'] == 'volc.seedasr.auc'
    assert submit_headers['x-api-request-id'] == query_headers['x-api-request-id']
    assert submit_headers['x-api-key'] == 'TEST-KEY-DO-NOT-LOG'
    assert 'TEST-KEY' not in submit.data.decode()
    body = json.loads(submit.data)
    wav_data = base64.b64decode(body['audio']['data'])
    assert body['request']['model_name'] == 'bigmodel'
    with wave.open(io.BytesIO(wav_data)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.readframes(16000) == pcm
    local.transcribe.assert_not_called()


def test_pending_query_is_polled_without_resubmitting(setup, monkeypatch):
    store, local, keys = setup
    opener = Mock()
    opener.open.side_effect = [
        _response({}, '20000000'),
        _response({}, '20000001'),
        _response({'result': {'text': '识别完成'}}, '20000000'),
    ]
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    monkeypatch.setattr('codex_whip.cloud_speech.time.sleep', lambda _: None)
    assert SpeechRouter(store, local, keys).transcribe(
        16000, struct.pack('<h', 500) * 8000) == '识别完成'
    assert opener.open.call_count == 3


def test_missing_key_uses_local_recognizer(setup):
    store, local, keys = setup
    keys.get.return_value = ''
    local.transcribe.return_value = '本地结果'
    assert SpeechRouter(store, local, keys).transcribe(16000, b'\0\0') == '本地结果'
    local.prepare.assert_called_once()


def test_cloud_failure_prepares_and_uses_local_fallback(setup, monkeypatch):
    store, local, keys = setup
    local.transcribe.return_value = '本地后备'
    opener = Mock()
    opener.open.side_effect = urllib.error.URLError('offline')
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    result = SpeechRouter(store, local, keys).transcribe(
        16000, struct.pack('<h', 500) * 8000)
    assert result == '本地后备'
    local.prepare.assert_called_once()


def test_cloud_error_is_preserved_when_local_also_fails(setup, monkeypatch):
    store, local, keys = setup
    local.prepare.side_effect = RuntimeError('local failure')
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(
        PRESETS[PRODUCT_PROVIDER].url, 403, 'TEST-KEY-DO-NOT-LOG', {}, None)
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    with pytest.raises(SpeechError) as exc:
        SpeechRouter(store, local, keys).transcribe(
            16000, struct.pack('<h', 500) * 8000)
    assert 'TEST-KEY' not in str(exc.value)
    assert '录音文件识别 2.0' in str(exc.value)


def test_silence_does_not_upload(setup, monkeypatch):
    store, local, keys = setup
    network = Mock(side_effect=AssertionError('unexpected upload'))
    monkeypatch.setattr('urllib.request.build_opener', network)
    assert SpeechRouter(store, local, keys).transcribe(16000, b'\0\0' * 16000) == ''
    network.assert_not_called()
    assert _NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test') is None


def test_bundled_key_prefers_environment(monkeypatch):
    monkeypatch.setenv('CODEX_WHIP_DOUBAO_API_KEY', 'bundled-test-key')
    assert bundled_doubao_key() == 'bundled-test-key'
    assert SpeechKeys().get(PRODUCT_PROVIDER) == 'bundled-test-key'


def test_unknown_provider_is_rejected_and_old_provider_migrates(tmp_path):
    with pytest.raises(ValueError):
        VoiceSettings(speech_provider='openai').validated()
    path = tmp_path / 'voice.json'
    path.write_text(json.dumps({'speech_provider': 'doubao-legacy'}), encoding='utf-8')
    assert VoiceSettingsStore(path).settings.speech_provider == PRODUCT_PROVIDER
