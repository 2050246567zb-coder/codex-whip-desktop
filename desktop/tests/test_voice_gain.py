import asyncio
import math
import struct
import wave
from dataclasses import replace
from unittest.mock import Mock

import pytest

from codex_whip.voice_gain import apply_recording_gain
from codex_whip.voice import VoiceSettings, VoiceSettingsStore, VoiceModule
from codex_whip.voice_replay import recording_path
from codex_whip.models import AudioStart, AudioChunk, AudioEnd


def unpack(pcm): return [v[0] for v in struct.iter_unpack('<h',pcm)]


def test_gain_scales_quiet_audio_and_preserves_silence():
    assert unpack(apply_recording_gain(struct.pack('<hhh',100,-200,0),4)) == [400,-800,0]
    for value in (0,1,15):
        pcm=struct.pack('<h',value)*8000
        assert apply_recording_gain(pcm,8)==pcm


def test_peak_limit_uses_single_linear_scale_without_clipping():
    pcm=struct.pack('<hhhh',-32768,16384,0,8192)
    assert unpack(apply_recording_gain(pcm,8))==[-30000,15000,0,7500]
    assert apply_recording_gain(pcm,1)==pcm


@pytest.mark.parametrize('gain',[0,9,-1,math.nan,math.inf,True,'2'])
def test_invalid_gain_rejected(gain):
    with pytest.raises(ValueError): VoiceSettings(recording_gain=gain).validated()
    with pytest.raises(ValueError): apply_recording_gain(bytes(10),gain)


def test_gain_is_persisted_and_old_profiles_are_discarded(tmp_path):
    path=tmp_path/'voice.json'
    path.write_text('{"schema_version":1,"enabled":true}')
    store=VoiceSettingsStore(path)
    assert store.settings.recording_gain==2 and store.settings.enabled
    store.update(replace(store.settings,recording_gain=4.5))
    assert VoiceSettingsStore(path).settings.recording_gain==4.5


def test_gain_snapshot_and_replay_match_actual_asr_audio(tmp_path):
    store=VoiceSettingsStore(tmp_path/'voice.json')
    store.update(replace(store.settings,recording_gain=4))
    transcriber=Mock()
    transcriber.transcribe.return_value='继续'
    module=VoiceModule(store,lambda *_:None,transcriber)
    async def run():
        await module.handle_audio(AudioStart(1,16000,'IMA_ADPCM4'))
        store.update(replace(store.settings,recording_gain=8))
        for i in range(25):
            await module.handle_audio(AudioChunk(1,i,320,100,0,bytes(160)))
        original=bytes(module.assembler.pcm)
        await module.handle_audio(AudioEnd(1,8000,'SILENCE'))
        return original
    original=asyncio.run(run())
    actual=transcriber.transcribe.call_args.args[1]
    assert actual==apply_recording_gain(original,4)
    with wave.open(str(recording_path(store.path))) as wav:
        assert wav.readframes(wav.getnframes())==actual
