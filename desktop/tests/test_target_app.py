from codex_whip.settings import CodexSettings
from codex_whip.senders.windows_uia import (
    is_target_executable, ControlCandidate, Rectangle, score_composer_candidate,
    composer_identity, WindowsCodexSender,
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
