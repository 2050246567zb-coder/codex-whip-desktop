from __future__ import annotations

from types import SimpleNamespace

import pytest

from codex_whip import macos_api
from codex_whip.senders import macos_ax
from codex_whip.settings import CodexSettings


def _fake_accessibility(monkeypatch, *, value: str, placeholder: str = "Message Codex"):
    quartz = SimpleNamespace(
        kAXRoleAttribute="role",
        kAXPositionAttribute="position",
        kAXSizeAttribute="size",
        kAXTitleAttribute="title",
        kAXDescriptionAttribute="description",
        kAXHelpAttribute="help",
        kAXValueAttribute="value",
        kAXPlaceholderValueAttribute="placeholder",
        kAXFocusedAttribute="focused",
        kAXPressAction="press",
        kAXErrorSuccess=0,
    )
    window_element = object()
    composer = object()
    attributes = {
        composer: {
            "role": "AXTextArea",
            "position": (250.0, 720.0),
            "size": (700.0, 90.0),
            "title": "",
            "description": "Message Codex",
            "help": "",
            "value": value,
            "placeholder": placeholder,
        }
    }
    window = macos_api.MacWindow(321, "Codex", 0, 0, 1200, 900, window_element)

    monkeypatch.setattr(macos_ax.sys, "platform", "darwin")
    monkeypatch.setattr(macos_api, "accessibility_trusted", lambda **_: True)
    monkeypatch.setattr(macos_api, "codex_windows", lambda: [window])
    monkeypatch.setattr(macos_api, "ax_descendants", lambda _root: [composer])
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (SimpleNamespace(), quartz))
    monkeypatch.setattr(
        macos_api,
        "_ax_point",
        lambda raw: raw if isinstance(raw, tuple) and len(raw) == 2 else None,
    )
    monkeypatch.setattr(
        macos_api,
        "_ax_size",
        lambda raw: raw if isinstance(raw, tuple) and len(raw) == 2 else None,
    )
    monkeypatch.setattr(
        macos_api,
        "ax_copy",
        lambda element, attribute: attributes.get(element, {}).get(attribute),
    )
    return attributes, composer, window


def test_macos_placeholder_is_treated_as_empty(monkeypatch) -> None:
    _fake_accessibility(monkeypatch, value="Message Codex")
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    ready = sender.check_ready()

    assert ready["composer_empty"] is True


def test_macos_sender_refuses_existing_draft(monkeypatch) -> None:
    _fake_accessibility(monkeypatch, value="未发送草稿")
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    with pytest.raises(macos_ax.CodexTargetError, match="草稿"):
        sender.check_ready()


def test_macos_sender_refuses_when_value_cannot_be_read(monkeypatch) -> None:
    _fake_accessibility(monkeypatch, value=None)  # type: ignore[arg-type]
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    with pytest.raises(macos_ax.CodexTargetError, match="是否为空"):
        sender.check_ready()


def test_macos_target_requires_an_openai_bundle_identifier() -> None:
    assert macos_api.is_official_codex_identity("Codex", "com.openai.codex")
    assert macos_api.is_official_codex_identity("ChatGPT", "com.openai.chat")
    assert not macos_api.is_official_codex_identity("Codex", "com.example.codex")
    assert not macos_api.is_official_codex_identity("Notes", "com.openai.notes")
