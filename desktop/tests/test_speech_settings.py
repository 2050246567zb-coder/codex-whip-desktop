import tkinter as tk
from unittest.mock import Mock
from dataclasses import replace
import pytest
from codex_whip.speech_settings import SpeechServiceCard
from codex_whip.cloud_speech import PRESETS, LOCAL_LABEL
from codex_whip.voice import VoiceSettingsStore


class MissingVirtualDriver:
    def detect(self):
        from codex_whip.virtual_microphone import VirtualMicrophoneError
        raise VirtualMicrophoneError('missing')


class FakeDriverInstaller:
    product_name = 'Test Virtual Audio'
    source_url = 'https://example.test/official'


def test_card_cloud_opt_in_masking_and_switching(tmp_path,monkeypatch):
    root=tk.Tk()
    root.configure(bg='#F5F5F7')
    root.withdraw()
    store=VoiceSettingsStore(tmp_path/'voice.json')
    keys=Mock()
    keys.get.return_value=''
    def apply(settings):
        store.update(settings)
        return True
    card=SpeechServiceCard(root,store,apply,keys=keys)
    try:
        assert not card.key_row.winfo_manager()
        card.choice.set(PRESETS['qwen-cn'].label)
        card.changed()
        assert card.entry.cget('show') == '•'
        card.key.set('test-key')
        monkeypatch.setattr('codex_whip.speech_settings.messagebox.askyesno',lambda *a,**k:False)
        card.save()
        assert store.settings.speech_provider=='local'
        keys.set.assert_not_called()
        monkeypatch.setattr('codex_whip.speech_settings.messagebox.askyesno',lambda *a,**k:True)
        card.save()
        assert store.settings.speech_provider=='qwen-cn'
        keys.set.assert_called_once_with('qwen-cn','test-key')
        assert card.key.get()==''
        card.key.set('must-not-carry')
        card.choice.set(PRESETS['openai'].label)
        card.changed()
        assert card.key.get()==''
        card.choice.set(LOCAL_LABEL)
        card.changed()
        card.save()
        assert store.settings.speech_provider=='local'
    finally:
        root.destroy()


def test_doubao_legacy_credentials_gain_and_switch_clear(tmp_path, monkeypatch):
    from codex_whip.cloud_speech import decode_doubao_credentials
    root=tk.Tk()
    root.configure(bg='#F5F5F7')
    root.withdraw()
    store=VoiceSettingsStore(tmp_path/'voice.json')
    keys=Mock()
    keys.get.return_value=''
    def apply(settings):
        store.update(settings)
        return True
    card=SpeechServiceCard(root,store,apply,keys=keys)
    monkeypatch.setattr('codex_whip.speech_settings.messagebox.askyesno',lambda *a,**k:True)
    try:
        card.choice.set(PRESETS['doubao-legacy'].label)
        card.changed()
        assert card.app_id_row.winfo_manager()=='pack'
        assert card.key_label.cget('text')=='Access Token'
        card.app_id.set('123456')
        card.key.set('TEST-TOKEN')
        card.gain_slider.set(4.3)
        card.save()
        assert store.settings.speech_provider=='doubao-legacy'
        assert store.settings.recording_gain==pytest.approx(4.3)
        preset, credential=keys.set.call_args.args
        assert preset=='doubao-legacy'
        assert decode_doubao_credentials(credential)==('123456','TEST-TOKEN')
        assert 'TEST-TOKEN' not in store.path.read_text()
        assert card.app_id.get()=='' and card.key.get()==''
        card.app_id.set('654321')
        card.key.set('old-token')
        card.choice.set(PRESETS['doubao'].label)
        card.changed()
        assert not card.app_id_row.winfo_manager()
        assert card.key_label.cget('text')=='API Key'
        assert card.app_id.get()=='' and card.key.get()==''
    finally:
        root.destroy()


def test_native_dictation_mode_is_retired_and_driver_controls_stay_hidden(tmp_path):
    root=tk.Tk()
    root.configure(bg='#F5F5F7')
    root.withdraw()
    store=VoiceSettingsStore(tmp_path/'voice.json')
    card=SpeechServiceCard(root,store,lambda _:True,keys=Mock(),
        driver_installer=FakeDriverInstaller(),virtual_bridge=MissingVirtualDriver())
    try:
        assert not card.driver_panel.winfo_manager()
        card.mode.set('virtual_microphone')
        card.mode_changed()
        assert card.mode.get() == 'transcription'
        assert not card.driver_panel.winfo_manager()
    finally:
        root.destroy()
