import tkinter as tk
from unittest.mock import Mock
from dataclasses import replace
import pytest
from codex_whip.speech_settings import SpeechServiceCard
from codex_whip.cloud_speech import PRESETS, LOCAL_LABEL
from codex_whip.voice import VoiceSettingsStore


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
