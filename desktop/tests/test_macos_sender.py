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


def test_macos_dictation_stops_the_exact_button_that_started_it(monkeypatch) -> None:
    attributes, composer, window = _fake_accessibility(monkeypatch, value="Message Codex")
    button = object()
    attributes[button] = {
        "role": "AXButton", "position": (980.0, 760.0), "size": (36.0, 36.0),
        "title": "", "description": "Dictate", "help": "",
    }
    monkeypatch.setattr(macos_api, "ax_descendants", lambda _root: [composer, button])
    monkeypatch.setattr(macos_api, "activate_application", lambda _pid: True)
    monkeypatch.setattr(macos_api, "window_for_pid", lambda _pid: window)
    pressed = []
    _appkit, quartz = macos_api._frameworks()
    quartz.AXUIElementPerformAction = lambda element, action: pressed.append((element, action)) or 0
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    session = sender.start_dictation()
    sender.stop_dictation(session)

    assert pressed == [(button, "press"), (button, "press")]


@pytest.mark.skipif(macos_api.sys.platform != "darwin", reason="requires macOS frameworks")
def test_real_macos_accessibility_bridge() -> None:
    """Exercise real symbols: mocks previously hid the incorrect Quartz import."""
    _appkit, services = macos_api._frameworks()
    assert isinstance(macos_api.accessibility_trusted(prompt=False), bool)
    point = services.AXValueCreate(services.kAXValueCGPointType, services.CGPoint(32, 48))
    size = services.AXValueCreate(services.kAXValueCGSizeType, services.CGSize(640, 480))
    assert macos_api._ax_point(point) == (32.0, 48.0)
    assert macos_api._ax_size(size) == (640.0, 480.0)
    assert callable(services.AXUIElementCopyAttributeValue)
    assert callable(services.AXUIElementSetAttributeValue)
    assert callable(services.AXUIElementPerformAction)
    assert callable(services.CGEventCreate)


@pytest.mark.parametrize("subrole", ["AXSystemDialog", "AXFloatingWindow", None])
def test_macos_ignores_auxiliary_windows(monkeypatch, subrole) -> None:
    services = SimpleNamespace(kAXSubroleAttribute="subrole", kAXStandardWindowSubrole="AXStandardWindow")
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))
    monkeypatch.setattr(macos_api, "ax_copy", lambda element, attribute: subrole)
    assert macos_api._window_from_element(321, object()) is None


def test_macos_keeps_standard_document_window(monkeypatch) -> None:
    services = SimpleNamespace(kAXSubroleAttribute="subrole", kAXStandardWindowSubrole="AXStandardWindow",
        kAXMinimizedAttribute="minimized", kAXPositionAttribute="position",
        kAXSizeAttribute="size", kAXTitleAttribute="title")
    attrs = dict(subrole="AXStandardWindow", minimized=False, position=(420, 94), size=(1090, 760), title="ChatGPT")
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))
    monkeypatch.setattr(macos_api, "ax_copy", lambda element, attribute: attrs.get(attribute))
    monkeypatch.setattr(macos_api, "_ax_point", lambda value: value)
    monkeypatch.setattr(macos_api, "_ax_size", lambda value: value)
    window = macos_api._window_from_element(321, object())
    assert window is not None
    assert (window.pid, window.title, window.width, window.height) == (321, "ChatGPT", 1090, 760)


@pytest.mark.parametrize("use_factory", [False, True])
def test_repeated_sender_creation_never_prompts_by_default(monkeypatch, use_factory) -> None:
    from codex_whip.senders.platform import create_live_sender
    prompts = []
    def trust(*, prompt=False):
        prompts.append(prompt)
        return False
    monkeypatch.setattr(macos_ax.sys, "platform", "darwin")
    monkeypatch.setattr(macos_api, "accessibility_trusted", trust)
    create = create_live_sender if use_factory else macos_ax.MacOSCodexSender
    for _ in range(5):
        with pytest.raises(macos_ax.CodexTargetError, match="系统设置"):
            create(CodexSettings())
    assert prompts == [False] * 5


def test_sender_recovers_after_permission_granted_without_prompt(monkeypatch) -> None:
    from codex_whip.senders.platform import create_live_sender
    granted = iter([False, True])
    prompts = []
    def trust(*, prompt=False):
        prompts.append(prompt)
        return next(granted)
    monkeypatch.setattr(macos_ax.sys, "platform", "darwin")
    monkeypatch.setattr(macos_api, "accessibility_trusted", trust)
    with pytest.raises(macos_ax.CodexTargetError):
        create_live_sender(CodexSettings())
    assert isinstance(create_live_sender(CodexSettings()), macos_ax.MacOSCodexSender)
    assert prompts == [False, False]


def test_configuring_hidden_overlay_does_not_show_it(monkeypatch):
    from unittest.mock import Mock
    window = Mock()
    window.title.return_value = 'Codex Whip Red Eyes'
    appkit = SimpleNamespace(NSApp=SimpleNamespace(windows=lambda: [window]))
    monkeypatch.setattr(macos_api, '_frameworks', lambda: (appkit, None))
    assert macos_api.configure_tk_window('Codex Whip Red Eyes', click_through=True, transparent=False)
    window.orderFrontRegardless.assert_not_called()


def test_overlay_window_snapshot_is_reused_and_expires(monkeypatch):
    from unittest.mock import Mock
    clock = [0.0]
    monkeypatch.setattr(macos_api.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(macos_api, '_window_cache', {})
    read = Mock(side_effect=['first', 'moved'])
    monkeypatch.setattr(macos_api, '_read_window_for_pid', read)
    assert macos_api.window_for_pid(123) == 'first'
    clock[0] = .02
    assert macos_api.window_for_pid(123) == 'first'
    assert read.call_count == 1
    clock[0] = .06
    assert macos_api.window_for_pid(123) == 'moved'
