import tkinter as tk
from unittest.mock import Mock

import pytest

from codex_whip.cloud_speech import PRODUCT_PROVIDER
from codex_whip.speech_settings import SpeechServiceCard
from codex_whip.voice import VoiceSettingsStore


class MissingVirtualDriver:
    def detect(self):
        from codex_whip.virtual_microphone import VirtualMicrophoneError
        raise VirtualMicrophoneError('missing')


class FakeDriverInstaller:
    product_name = 'Test Virtual Audio'
    source_url = 'https://example.test/official'


def test_card_exposes_only_latest_service_and_saves_gain(tmp_path):
    root = tk.Tk()
    root.configure(bg='#F5F5F7')
    root.withdraw()
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    keys = Mock()
    keys.get.return_value = ''

    def apply(settings):
        store.update(settings)
        return True

    card = SpeechServiceCard(root, store, apply, keys=keys)
    try:
        assert card.selected == PRODUCT_PROVIDER
        assert not hasattr(card, 'select')
        assert not hasattr(card, 'app_id')
        assert card.entry.cget('show') == '•'
        card.key.set('test-key')
        card.gain_slider.set(4.3)
        card.save()
        assert store.settings.speech_provider == PRODUCT_PROVIDER
        assert store.settings.recording_gain == pytest.approx(4.3)
        keys.set.assert_called_once_with(PRODUCT_PROVIDER, 'test-key')
        assert card.key.get() == ''
    finally:
        root.destroy()


def test_old_voice_modes_are_not_exposed(tmp_path):
    root = tk.Tk()
    root.configure(bg='#F5F5F7')
    root.withdraw()
    store = VoiceSettingsStore(tmp_path / 'voice.json')
    card = SpeechServiceCard(
        root, store, lambda _: True, keys=Mock(),
        driver_installer=FakeDriverInstaller(), virtual_bridge=MissingVirtualDriver())
    try:
        assert card.mode.get() == 'transcription'
        assert not card.driver_panel.winfo_manager()
        card.mode.set('virtual_microphone')
        card.mode_changed()
        assert card.mode.get() == 'transcription'
        assert not card.driver_panel.winfo_manager()
    finally:
        root.destroy()
