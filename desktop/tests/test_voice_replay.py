import asyncio
import wave
from unittest.mock import Mock

import pytest

from codex_whip.models import AudioStart, AudioChunk, AudioEnd
from codex_whip.voice import VoiceModule, VoiceSettingsStore
from codex_whip.voice_replay import recording_path, recording_duration, save_recording, RecordingPlayer


def test_recording_is_exact_pcm_and_replaced_atomically(tmp_path):
    path = tmp_path/'last.wav'
    assert recording_duration(path) is None
    for pcm in (b'\x01\x00'*8000, b'\x02\x00'*16000):
        save_recording(path, 16000, pcm)
        with wave.open(str(path), 'rb') as f:
            assert f.getnchannels() == 1 and f.getsampwidth() == 2
            assert f.readframes(f.getnframes()) == pcm
        assert recording_duration(path) == len(pcm)/32000
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize('result', ['你好', '', RuntimeError('识别服务不可用')])
def test_replay_keeps_asr_input_even_for_empty_or_failed_recognition(tmp_path, result):
    transcriber = Mock()
    if isinstance(result, Exception):
        transcriber.transcribe.side_effect = result
    else:
        transcriber.transcribe.return_value = result
    store = VoiceSettingsStore(tmp_path/'voice.json')
    module = VoiceModule(store, lambda *_: None, transcriber)
    async def record():
        await module.handle_audio(AudioStart(1,16000,'IMA_ADPCM4'))
        for i in range(25):
            await module.handle_audio(AudioChunk(1,i,320,20,0,bytes(160)))
        await module.handle_audio(AudioEnd(1,8000,'SILENCE'))
    asyncio.run(record())
    with wave.open(str(recording_path(store.path)), 'rb') as f:
        assert f.readframes(f.getnframes()) == transcriber.transcribe.call_args.args[1]
    assert recording_duration(recording_path(store.path)) == .5


def test_failed_capture_does_not_replace_last_asr_recording(tmp_path):
    store = VoiceSettingsStore(tmp_path/'voice.json')
    path = recording_path(store.path)
    save_recording(path,16000,b'\x02\x00'*8000)
    original = path.read_bytes()
    transcriber = Mock()
    module = VoiceModule(store,lambda *_: None, transcriber)
    async def record():
        await module.handle_audio(AudioStart(2,16000,'IMA_ADPCM4'))
        await module.handle_audio(AudioChunk(2,0,320,0,0,bytes(160)))
        await module.handle_audio(AudioEnd(2,320,'TX_FAILED'))
    asyncio.run(record())
    assert path.read_bytes() == original
    transcriber.transcribe.assert_not_called()


def test_replay_storage_error_does_not_stop_asr(tmp_path, monkeypatch):
    from codex_whip import voice_replay
    monkeypatch.setattr(voice_replay,'save_recording',Mock(side_effect=OSError('disk full')))
    events=[]
    transcriber=Mock()
    transcriber.transcribe.return_value='继续'
    module=VoiceModule(VoiceSettingsStore(tmp_path/'voice.json'),lambda *x:events.append(x),transcriber)
    module.assembler.begin(AudioStart(1,16000,'IMA_ADPCM4'))
    for i in range(25): module.assembler.add(AudioChunk(1,i,320,0,0,bytes(160)))
    asyncio.run(module.handle_audio(AudioEnd(1,8000,'SILENCE')))
    assert module.pending_text == '继续'
    assert any(k=='log' and '保存失败' in v for k,v in events)


def test_windows_player_uses_private_snapshot_and_closes_only_own_alias(tmp_path, monkeypatch):
    import os
    if os.name != 'nt': pytest.skip('Windows playback command contract')
    path=tmp_path/'last.wav'
    save_recording(path,16000,bytes(32000))
    player=RecordingPlayer()
    commands=[]
    def command(value):
        commands.append(value)
        return 'playing' if value.startswith('status') else ''
    monkeypatch.setattr(player,'_mci',command)
    player.play(path)
    snapshot=player._snapshot
    assert snapshot != path and snapshot.exists()
    assert player.playing
    save_recording(path,16000,b'\x01\x00'*8000)
    assert recording_duration(snapshot) == 1
    player.stop()
    assert not snapshot.exists() and path.exists()
    assert commands[-1] == f'close {player._alias}'


def test_replay_card_disabled_play_stop_new_recording_and_destroy(tmp_path):
    import tkinter as tk
    from codex_whip.speech_settings import SpeechServiceCard
    root=tk.Tk()
    root.configure(bg='#F6F7F9')
    root.withdraw()
    store=VoiceSettingsStore(tmp_path/'voice.json')
    card=SpeechServiceCard(root,store,lambda _:True, keys=Mock())
    player=Mock()
    player.playing=True
    card._player=player
    def refresh():
        card.card.after_cancel(card._replay_after)
        card._refresh_replay()
    try:
        assert card.replay_button.cget('state') == 'disabled'
        save_recording(recording_path(store.path),16000,bytes(16000))
        refresh()
        assert card.replay_button.cget('state') == 'normal'
        card.toggle_replay()
        player.play.assert_called_once_with(recording_path(store.path))
        assert card.replay_button.cget('text') == '停止播放'
        card.toggle_replay()
        assert card.replay_button.cget('text') == '播放上次录音'
        card.toggle_replay()
        card.recording_active=lambda:True
        refresh()
        assert not card._replay_active
        assert card.replay_button.cget('state') == 'disabled'
        card.recording_active=lambda:False
        refresh()
        assert card.replay_button.cget('state') == 'normal'
    finally:
        root.destroy()
    assert card._replay_after is None
    assert player.stop.called
