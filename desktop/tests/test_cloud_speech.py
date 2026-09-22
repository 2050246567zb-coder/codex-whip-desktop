import io
import json
import struct
import urllib.error
import wave
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from codex_whip.cloud_speech import PRESETS, SpeechKeys, SpeechRouter, SpeechError, _NoRedirect, encode_doubao_credentials
from codex_whip.voice import VoiceSettings, VoiceSettingsStore


@pytest.fixture
def setup(tmp_path):
    store = VoiceSettingsStore(tmp_path/'voice.json')
    local = Mock()
    keys = Mock()
    keys.get.return_value = 'TEST-KEY-DO-NOT-LOG'
    return store, local, keys


@pytest.mark.parametrize('provider', list(PRESETS))
def test_provider_request_and_response(setup, monkeypatch, provider):
    store, local, keys = setup
    store.update(replace(store.settings, speech_provider=provider))
    router = SpeechRouter(store,local,keys)
    result = {'choices':[{'message':{'content':'继续完成任务'}}]} if PRESETS[provider].protocol=='qwen' else {'text':'继续完成任务'}
    doubao = provider.startswith('doubao')
    if doubao:
        result = {'result': {'text': '继续完成任务'}}
    if provider == 'doubao-legacy':
        keys.get.return_value = encode_doubao_credentials('123456','TEST-KEY-DO-NOT-LOG')
    opener = Mock()
    response = io.BytesIO(json.dumps(result).encode())
    response.headers = {'X-Api-Status-Code': '20000000'}
    opener.open.return_value.__enter__ = Mock(return_value=response)
    opener.open.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr('urllib.request.build_opener', lambda *_:opener)
    pcm = struct.pack('<h',800)*16000
    assert router.transcribe(16000,pcm) == '继续完成任务'
    request = opener.open.call_args.args[0]
    assert request.full_url == PRESETS[provider].url
    headers = {k.lower(): v for k,v in request.headers.items()}
    if doubao:
        assert 'authorization' not in headers
        assert headers['x-api-resource-id'] == 'volc.bigasr.auc_turbo'
        assert headers['x-api-sequence'] == '-1'
        if provider == 'doubao-legacy':
            assert headers['x-api-app-key'] == '123456'
            assert headers['x-api-access-key'] == 'TEST-KEY-DO-NOT-LOG'
            assert 'x-api-key' not in headers
        else:
            assert headers['x-api-key'] == 'TEST-KEY-DO-NOT-LOG'
            assert 'x-api-app-key' not in headers
    else:
        assert request.headers['Authorization'] == 'Bearer TEST-KEY-DO-NOT-LOG'
    assert opener.open.call_args.kwargs['timeout'] == 30
    if doubao:
        import base64
        body = json.loads(request.data)
        wavdata = base64.b64decode(body['audio']['data'])
        assert body['request']['model_name'] == 'bigmodel'
        assert 'TEST-KEY' not in request.data.decode()
    elif PRESETS[provider].protocol == 'qwen':
        import base64
        body = json.loads(request.data)
        wavdata = base64.b64decode(body['messages'][0]['content'][0]['input_audio']['data'].split(',')[1])
    else:
        wavdata = request.data[request.data.index(b'RIFF'):].split(b'\r\n--Whip')[0]
        assert PRESETS[provider].model.encode() in request.data
    with wave.open(io.BytesIO(wavdata)) as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.readframes(16000) == pcm
    local.transcribe.assert_not_called()


def test_local_never_reads_key_or_network_and_snapshot_pins_provider(setup,monkeypatch):
    store, local, keys = setup
    router = SpeechRouter(store,local,keys)
    snapshot = router.snapshot()
    store.update(replace(store.settings,speech_provider='openai'))
    local.transcribe.return_value = 'local'
    assert snapshot.transcribe(16000,b'\0\0') == 'local'
    keys.get.assert_not_called()


@pytest.mark.parametrize('code', [401,403,404,429,500,302])
def test_errors_never_expose_keys_or_response_and_never_retry(setup,monkeypatch,code):
    store,local,keys = setup
    store.update(replace(store.settings,speech_provider='openai-mini'))
    opener = Mock()
    opener.open.side_effect = urllib.error.HTTPError(PRESETS['openai-mini'].url,code,'TEST-KEY-DO-NOT-LOG',{},None)
    monkeypatch.setattr('urllib.request.build_opener',lambda *_:opener)
    with pytest.raises(SpeechError) as exc:
        SpeechRouter(store,local,keys).transcribe(16000,struct.pack('<h',500)*8000)
    assert 'TEST-KEY' not in str(exc.value)
    assert opener.open.call_count == 1
    local.transcribe.assert_not_called()


def test_silence_does_not_upload_and_config_excludes_secrets(setup,monkeypatch):
    store,local,keys=setup
    store.update(replace(store.settings,speech_provider='siliconflow'))
    network=Mock(side_effect=AssertionError('unexpected upload'))
    monkeypatch.setattr('urllib.request.build_opener',network)
    assert SpeechRouter(store,local,keys).transcribe(16000,b'\0\0'*16000) == ''
    network.assert_not_called()
    assert 'TEST-KEY' not in store.path.read_text()
    assert VoiceSettingsStore(store.path).settings.speech_provider=='siliconflow'
    assert _NoRedirect().redirect_request(None,None,302,'',{},'https://other.test') is None


def test_vault_failure_is_safe(monkeypatch):
    keys=SpeechKeys()
    backend=Mock()
    backend.set_password.side_effect=RuntimeError('secret-key-value')
    monkeypatch.setattr(keys,'_backend',lambda:backend)
    with pytest.raises(SpeechError) as exc:
        keys.set('openai','secret-key-value')
    assert 'secret-key-value' not in str(exc.value)


def test_unknown_preset_rejected():
    with pytest.raises(ValueError):
        VoiceSettings(speech_provider='arbitrary-host').validated()


@pytest.mark.parametrize('code', ['20000003','45000001','45000002','45000151','55000031','unknown',''])
def test_doubao_checks_service_status_even_with_http_success(setup,monkeypatch,code):
    store,local,keys=setup
    store.update(replace(store.settings,speech_provider='doubao'))
    response=io.BytesIO(b'{"result":{"text":"must not send"}}')
    response.headers={'X-Api-Status-Code':code,'X-Api-Message':'TEST-KEY-DO-NOT-LOG'}
    opener=Mock()
    opener.open.return_value.__enter__=Mock(return_value=response)
    opener.open.return_value.__exit__=Mock(return_value=False)
    monkeypatch.setattr('urllib.request.build_opener',lambda *_:opener)
    router=SpeechRouter(store,local,keys)
    if code=='20000003':
        assert router.transcribe(16000,struct.pack('<h',500)*8000)==''
    else:
        with pytest.raises(SpeechError) as exc:
            router.transcribe(16000,struct.pack('<h',500)*8000)
        assert 'TEST-KEY' not in str(exc.value)
    assert opener.open.call_count==1
    local.transcribe.assert_not_called()


def test_bad_legacy_credentials_never_upload(setup,monkeypatch):
    store,local,keys=setup
    store.update(replace(store.settings,speech_provider='doubao-legacy'))
    keys.get.return_value='invalid'
    opener=Mock()
    monkeypatch.setattr('urllib.request.build_opener',opener)
    router=SpeechRouter(store,local,keys)
    assert not router.ready
    with pytest.raises(SpeechError):
        router.transcribe(16000,struct.pack('<h',500)*8000)
    opener.assert_not_called()


def test_missing_cloud_key_uses_ready_local_recognizer(setup):
    store, local, keys = setup
    store.update(replace(store.settings, speech_provider='openai-mini'))
    keys.get.return_value = ''
    local.ready = True
    local.transcribe.return_value = '本地结果'
    assert SpeechRouter(store, local, keys).transcribe(16000, b'\0\0') == '本地结果'


def test_cloud_failure_falls_back_only_when_local_is_already_ready(setup, monkeypatch):
    store, local, keys = setup
    store.update(replace(store.settings, speech_provider='openai-mini'))
    local.ready = True
    local.transcribe.return_value = '本地后备'
    opener = Mock()
    opener.open.side_effect = urllib.error.URLError('offline')
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    pcm = struct.pack('<h', 500) * 8000
    assert SpeechRouter(store, local, keys).transcribe(16000, pcm) == '本地后备'
    assert opener.open.call_count == 1
