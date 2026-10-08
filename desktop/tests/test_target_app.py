from codex_whip.settings import CodexSettings
import pytest
from codex_whip.senders import windows_uia
from codex_whip.senders.windows_uia import (
    is_target_executable, ControlCandidate, Rectangle, score_composer_candidate,
    composer_identity, WindowsCodexSender, WindowsDictationSession,
)


def test_claude_target_never_falls_back_to_codex():
    claude = CodexSettings(target_app='Claude')
    assert is_target_executable('C:/Apps/Claude/claude.exe', claude)
    assert not is_target_executable('C:/OpenAI.Codex_test/ChatGPT.exe', claude)
    assert not is_target_executable('C:/Apps/Claude/terminal.exe', claude)
    assert not is_target_executable('C:/Apps/Claude/claude.exe', CodexSettings())


def test_claude_new_chat_center_editor_requires_known_name():
    window = Rectangle(0, 0, 1000, 900)
    editor = ControlCandidate('Edit', 'Write your prompt to Claude', Rectangle(300,300,950,345))
    assert score_composer_candidate(editor, window, (), target_app='Claude') is not None
    assert score_composer_candidate(editor, window, ()) is None
    search = ControlCandidate('Edit', 'Search', editor.rectangle)
    assert score_composer_candidate(search, window, (), target_app='Claude') is None


def test_claude_identity_is_not_placeholder_or_draft_text():
    from types import SimpleNamespace
    control = SimpleNamespace(
        element_info=SimpleNamespace(name='Write your prompt to Claude', class_name='ProseMirror'),
        window_text=lambda: 'How can I help you today?',
        get_value=lambda: '',
    )
    assert composer_identity(control, 'Claude') == 'Write your prompt to Claude'
    sender = object.__new__(WindowsCodexSender)
    sender._settings = CodexSettings(target_app='Claude')
    assert sender._composer_value(control) == ''
    control.get_value = lambda: 'My unsent draft'
    assert sender._composer_value(control) == 'My unsent draft'


def test_window_lookup_filters_processes_before_creating_uia_wrappers(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    sender = object.__new__(WindowsCodexSender)
    sender._settings = CodexSettings()
    sender._visible_top_level_windows = lambda: [(101, 11), (202, 22)]
    desktop = Mock()
    target = Mock()
    target.window_text.return_value = 'Codex'
    desktop.window.return_value.wrapper_object.return_value = target
    sender._desktop = lambda: desktop
    executables = {
        11: 'C:/Program Files/WindowsApps/OpenAI.Codex_test/ChatGPT.exe',
        22: 'C:/Windows/notepad.exe',
    }
    monkeypatch.setattr(
        windows_uia.psutil,
        'Process',
        lambda pid: SimpleNamespace(exe=lambda: executables[pid]),
    )

    assert sender._codex_windows() == [target]
    desktop.window.assert_called_once_with(handle=101)
    desktop.windows.assert_not_called()


@pytest.mark.parametrize('auxiliary_style', [0x002800A8, 0x08000000])
def test_codex_pet_is_excluded_before_uia_and_main_window_remains_ready(
    monkeypatch, auxiliary_style,
):
    from types import SimpleNamespace
    from unittest.mock import Mock

    sender = object.__new__(WindowsCodexSender)
    sender._settings = CodexSettings()
    sender._visible_top_level_windows = lambda: [(202, 11), (101, 11)]
    desktop = Mock()
    main = Mock()
    main.handle = 101
    main.process_id.return_value = 11
    main.window_text.return_value = 'ChatGPT'
    main.rectangle.return_value = SimpleNamespace(left=0, top=0, right=1000, bottom=900)
    desktop.window.return_value.wrapper_object.return_value = main
    sender._desktop = lambda: desktop
    sender._find_composer = Mock(return_value=object())
    sender._composer_value = Mock(return_value='')
    monkeypatch.setattr(windows_uia.sys, 'platform', 'win32')
    monkeypatch.setattr(windows_uia, '_user32', SimpleNamespace(
        GetWindowLongW=lambda hwnd, index: auxiliary_style if hwnd == 202 else 0x00240100,
    ), raising=False)
    monkeypatch.setattr(windows_uia.psutil, 'Process', lambda pid: SimpleNamespace(
        exe=lambda: 'C:/Program Files/WindowsApps/OpenAI.Codex_test/ChatGPT.exe',
    ))

    assert sender.locate_window()['handle'] == 101
    assert sender.check_ready()['composer_empty'] is True
    assert sender.diagnose() == [{'title': 'ChatGPT', 'pid': 11, 'handle': 101}]
    assert all(call.kwargs == {'handle': 101} for call in desktop.window.call_args_list)


def test_two_real_codex_windows_remain_ambiguous():
    sender = object.__new__(WindowsCodexSender)
    sender._settings = CodexSettings()
    sender._codex_windows = lambda: [object(), object()]

    with pytest.raises(windows_uia.CodexTargetError, match='found 2'):
        sender.locate_window()


def test_stop_dictation_refinds_replacement_button_instead_of_stale_start():
    from unittest.mock import Mock

    sender = object.__new__(WindowsCodexSender)
    sender._settings = CodexSettings()
    window = Mock()
    window.handle = 101
    window.process_id.return_value = 11
    sender._codex_windows = lambda: [window]
    stale_start = Mock()
    active_stop = Mock()
    sender._find_stop_dictation_button = Mock(return_value=active_stop)

    sender.stop_dictation(WindowsDictationSession(101, 11, stale_start))

    sender._find_stop_dictation_button.assert_called_once_with(window)
    active_stop.invoke.assert_called_once_with()
    stale_start.invoke.assert_not_called()
