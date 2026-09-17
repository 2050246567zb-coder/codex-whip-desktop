import ctypes

from codex_whip.senders.windows_uia import (
    ControlCandidate,
    INPUT,
    Rectangle,
    _is_codex_executable,
    normalize_composer_value,
    score_composer_candidate,
)


def test_windows_input_structure_matches_native_abi() -> None:
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(INPUT) == expected


def test_bottom_edit_with_hint_is_preferred() -> None:
    window = Rectangle(0, 0, 1200, 900)
    composer = ControlCandidate("Edit", "Message Codex", Rectangle(250, 730, 1100, 840))
    document = ControlCandidate("Document", "Conversation", Rectangle(250, 100, 1100, 700))

    composer_score = score_composer_candidate(composer, window, ("message",))
    document_score = score_composer_candidate(document, window, ("message",))

    assert composer_score is not None
    assert document_score is None


def test_hidden_or_tiny_controls_are_rejected() -> None:
    window = Rectangle(0, 0, 1200, 900)
    hidden = ControlCandidate(
        "Edit", "Message", Rectangle(250, 730, 1100, 840), visible=False
    )
    tiny = ControlCandidate("Edit", "Message", Rectangle(900, 800, 950, 830))

    assert score_composer_candidate(hidden, window, ("message",)) is None
    assert score_composer_candidate(tiny, window, ("message",)) is None


def test_only_packaged_codex_executable_is_accepted() -> None:
    assert _is_codex_executable(
        r"C:\Program Files\WindowsApps\OpenAI.Codex_1.2.3_x64__id\app\ChatGPT.exe",
        "OpenAI.Codex_",
    )
    assert not _is_codex_executable(
        r"C:\Program Files\ChatGPT\ChatGPT.exe", "OpenAI.Codex_"
    )


def test_empty_prosemirror_placeholder_is_not_treated_as_a_draft() -> None:
    assert normalize_composer_value(
        "\n随心输入", "随心输入", "ProseMirror ProseMirror-focused"
    ) == ""


def test_real_composer_text_is_preserved() -> None:
    assert normalize_composer_value(
        "这是尚未发送的草稿", "随心输入", "ProseMirror ProseMirror-focused"
    ) == "这是尚未发送的草稿"
