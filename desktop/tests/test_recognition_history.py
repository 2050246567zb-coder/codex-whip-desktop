import asyncio
import io
import json
import urllib.error
from unittest.mock import Mock

from codex_whip.cloud_speech import SpeechRouter
from codex_whip.models import AudioChunk, AudioEnd, AudioStart
from codex_whip.recognition_history import RecognitionHistory
from codex_whip.voice import VoiceModule, VoiceSettings, VoiceSettingsStore


def _response(body, status):
    response = io.BytesIO(json.dumps(body).encode())
    response.headers = {'X-Api-Status-Code': status}
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    return context


async def _record(module, *, session=7, end_reason='SILENCE'):
    await module.handle_audio(AudioStart(session, 16000, 'IMA_ADPCM4'))
    for sequence in range(25):
        await module.handle_audio(AudioChunk(session, sequence, 320, 20, 0, bytes(160)))
    await module.handle_audio(AudioEnd(session, 8000, end_reason))


def _last_record(store):
    path = RecognitionHistory(store.path).path
    return json.loads(path.read_text(encoding='utf-8').splitlines()[-1])


def test_cloud_success_records_actual_engine_and_request_without_secret(tmp_path, monkeypatch):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    local = Mock(ready=True)
    keys = Mock()
    keys.get.return_value = 'SECRET-DO-NOT-PERSIST'
    opener = Mock()
    opener.open.side_effect = [
        _response({}, '20000000'),
        _response({'result': {'text': '继续完成任务'}}, '20000000'),
    ]
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    emitted = []
    module = VoiceModule(store, lambda *event: emitted.append(event), SpeechRouter(store, local, keys))

    asyncio.run(_record(module))

    record = _last_record(store)
    from codex_whip import __version__
    assert record['app_version'] == __version__
    assert record['result'] == 'success'
    assert record['transcript'] == '继续完成任务'
    assert record['audio']['duration_ms'] == 500
    assert record['audio']['last_recording_saved'] is True
    assert record['settings']['precise_recognition'] is True
    assert record['recognition']['final_engine'] == 'doubao-v2'
    assert record['recognition']['fallback'] is False
    assert record['recognition']['attempts'][0]['submitted'] is True
    assert record['recognition']['attempts'][0]['service_status_code'] == '20000000'
    assert record['recognition']['attempts'][0]['request_id']
    assert 'SECRET-DO-NOT-PERSIST' not in RecognitionHistory(store.path).path.read_text(encoding='utf-8')
    local.transcribe.assert_not_called()
    assert any(kind == 'log' and 'doubao-v2' in text for kind, text in emitted)


def test_cloud_failure_records_fallback_to_local(tmp_path, monkeypatch):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    local = Mock(ready=True)
    local.transcribe.return_value = '本地识别结果'
    keys = Mock()
    keys.get.return_value = 'SECRET-DO-NOT-PERSIST'
    opener = Mock()
    opener.open.side_effect = urllib.error.URLError('offline')
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    module = VoiceModule(store, lambda *_: None, SpeechRouter(store, local, keys))

    asyncio.run(_record(module))

    record = _last_record(store)
    assert record['result'] == 'success'
    assert record['recognition']['final_engine'] == 'whisper.cpp'
    assert record['recognition']['fallback'] is True
    assert [attempt['engine'] for attempt in record['recognition']['attempts']] == [
        'doubao-v2', 'whisper.cpp']
    assert record['recognition']['attempts'][0]['status'] == 'error'
    assert record['recognition']['attempts'][1]['status'] == 'success'
    assert 'SECRET-DO-NOT-PERSIST' not in RecognitionHistory(store.path).path.read_text(encoding='utf-8')


def test_local_only_and_capture_failure_are_both_recorded(tmp_path):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    store.update(VoiceSettings(precise_recognition=False))
    local = Mock(ready=True)
    local.transcribe.return_value = '本地结果'
    keys = Mock()
    keys.get.side_effect = AssertionError('local-only must not read key')
    module = VoiceModule(store, lambda *_: None, SpeechRouter(store, local, keys))

    asyncio.run(_record(module, session=1))
    first = _last_record(store)
    assert first['recognition']['final_engine'] == 'whisper.cpp'
    assert first['recognition']['cloud_skip_reason'] == 'precise_recognition_disabled'
    keys.get.assert_not_called()

    asyncio.run(_record(module, session=2, end_reason='TX_FAILED'))
    path = RecognitionHistory(store.path).path
    lines = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert len(lines) == 2
    assert lines[-1]['result'] == 'error'
    assert lines[-1]['error_stage'] == 'audio_assembly'
    assert lines[-1]['recognition']['attempts'] == []
    assert lines[-1]['session_id'] == 2


def test_history_write_failure_does_not_block_recognition(tmp_path, monkeypatch):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    transcriber = Mock()
    transcriber.transcribe.return_value = '继续'
    events = []
    module = VoiceModule(store, lambda *event: events.append(event), transcriber)
    monkeypatch.setattr(module.recognition_history, 'append', Mock(side_effect=OSError('disk full')))

    asyncio.run(_record(module))

    assert module.pending_text == '继续'
    assert any(kind == 'voice_state' and payload['state'] == 'ready'
               for kind, payload in events)
    assert any(kind == 'log' and '识别记录保存失败' in payload
               for kind, payload in events)


def test_cloud_and_local_failure_record_both_attempts_without_key(tmp_path, monkeypatch):
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    local = Mock(ready=False)
    local.prepare.side_effect = RuntimeError('local unavailable')
    keys = Mock()
    keys.get.return_value = 'SECRET-DO-NOT-PERSIST'
    opener = Mock()
    opener.open.side_effect = urllib.error.URLError('offline')
    monkeypatch.setattr('urllib.request.build_opener', lambda *_: opener)
    events = []
    module = VoiceModule(store, lambda *event: events.append(event), SpeechRouter(store, local, keys))

    asyncio.run(_record(module))

    record = _last_record(store)
    assert record['result'] == 'error'
    assert record['recognition']['final_engine'] is None
    assert record['recognition']['fallback'] is True
    assert [attempt['status'] for attempt in record['recognition']['attempts']] == [
        'error', 'error']
    assert 'SECRET-DO-NOT-PERSIST' not in RecognitionHistory(store.path).path.read_text(encoding='utf-8')
    assert any(kind == 'voice_error' for kind, _payload in events)
