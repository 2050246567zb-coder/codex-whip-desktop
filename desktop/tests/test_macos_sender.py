from __future__ import annotations

from contextlib import contextmanager
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


def test_open_privacy_settings_uses_matching_macos_pane(monkeypatch):
    opened = []
    workspace = SimpleNamespace(openURL_=lambda url: opened.append(url) or True)
    appkit = SimpleNamespace(
        NSURL=SimpleNamespace(URLWithString_=lambda value: value),
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace),
    )
    monkeypatch.setattr(macos_api, '_frameworks', lambda: (appkit, None))
    assert macos_api.open_privacy_settings('accessibility')
    assert macos_api.open_privacy_settings('bluetooth')
    assert opened == [
        'x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility',
        'x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth',
    ]


def test_macos_placeholder_is_treated_as_empty(monkeypatch) -> None:
    _fake_accessibility(monkeypatch, value="Message Codex")
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    ready = sender.check_ready()

    assert ready["composer_empty"] is True


def test_macos_sender_can_arm_without_overwriting_existing_draft(monkeypatch) -> None:
    _fake_accessibility(monkeypatch, value="未发送草稿")
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    assert sender.check_ready()["composer_empty"] is False
    with pytest.raises(macos_ax.CodexTargetError, match="草稿"):
        sender.send("新消息", None)


def test_macos_recognized_text_appends_and_sends_existing_draft(monkeypatch) -> None:
    attributes, composer, _window = _fake_accessibility(
        monkeypatch, value="原有十个字  ")
    events = []
    monkeypatch.setattr(macos_api, "activate_application", lambda _pid: True)
    monkeypatch.setattr(macos_api, "frontmost_pid", lambda: 321)
    monkeypatch.setattr(macos_api, "ax_set", lambda *_args: True)
    monkeypatch.setattr(macos_api, "post_command_end", lambda: events.append("end"))
    @contextmanager
    def clipboard(_text):
        yield
    monkeypatch.setattr(macos_api, "temporary_clipboard_text", clipboard)
    monkeypatch.setattr(macos_api, "post_command_paste", lambda: (
        events.append("paste"), attributes[composer].update(
            value=attributes[composer]["value"] + "识别的五个字")))
    monkeypatch.setattr(macos_api, "post_left_click", lambda x, y: events.append((x, y)))
    monkeypatch.setattr(macos_api, "post_return", lambda: pytest.fail("Return used"))
    monkeypatch.setattr(macos_api, "post_return_to_pid", lambda pid: events.append(("targeted-return", pid)))
    monkeypatch.setattr(macos_ax.time, "sleep", lambda _seconds: None)
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    result = sender.send_voice("识别的五个字", None)

    assert result.sent
    assert attributes[composer]["value"] == "原有十个字  识别的五个字"
    assert events == [(600, 765), "end", "paste", ("targeted-return", 321)]


def test_macos_voice_pastes_without_post_input_ax_verification(monkeypatch) -> None:
    attributes, composer, _window = _fake_accessibility(monkeypatch, value="已有草稿")
    events = []
    monkeypatch.setattr(macos_api, "activate_application", lambda _pid: True)
    monkeypatch.setattr(macos_api, "frontmost_pid", lambda: 321)
    monkeypatch.setattr(macos_api, "ax_set", lambda *_args: True)
    monkeypatch.setattr(macos_api, "post_command_end", lambda: None)
    @contextmanager
    def clipboard(_text):
        yield
    monkeypatch.setattr(macos_api, "temporary_clipboard_text", clipboard)
    monkeypatch.setattr(macos_api, "post_command_paste", lambda: attributes[composer].update(
        value="新语音" + attributes[composer]["value"]))
    monkeypatch.setattr(macos_api, "post_left_click", lambda *_point: events.append("click"))
    monkeypatch.setattr(macos_api, "post_return_to_pid", lambda _pid: events.append("targeted-return"))
    monkeypatch.setattr(macos_ax.time, "sleep", lambda _seconds: None)
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    result = sender.send_voice("新语音", None)
    assert result.sent
    assert result.text_may_be_inserted
    assert events == ["click", "targeted-return"]


def test_macos_voice_pastes_even_when_accessibility_value_is_stale(monkeypatch) -> None:
    attributes, composer, _window = _fake_accessibility(monkeypatch, value="已有草稿")
    events = []
    stale_value = macos_api.ax_copy
    monkeypatch.setattr(macos_api, "ax_copy", lambda element, attribute: (
        "已有草稿" if element is composer and attribute == "value"
        else stale_value(element, attribute)))
    monkeypatch.setattr(macos_api, "activate_application", lambda _pid: True)
    monkeypatch.setattr(macos_api, "frontmost_pid", lambda: 321)
    monkeypatch.setattr(macos_api, "ax_set", lambda *_args: True)
    monkeypatch.setattr(macos_api, "post_command_end", lambda: None)
    @contextmanager
    def clipboard(_text):
        yield
    monkeypatch.setattr(macos_api, "temporary_clipboard_text", clipboard)
    monkeypatch.setattr(macos_api, "post_command_paste", lambda: attributes[composer].update(
        value="已有草稿识别文字"))
    monkeypatch.setattr(macos_api, "post_left_click", lambda *_point: events.append("click"))
    monkeypatch.setattr(macos_api, "post_return_to_pid", lambda _pid: events.append("targeted-return"))
    monkeypatch.setattr(macos_ax.time, "sleep", lambda _seconds: None)
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    result = sender.send_voice("识别文字", None)

    assert result.sent
    assert result.text_may_be_inserted
    assert events == ["click", "targeted-return"]


def test_macos_targets_return_for_normal_message(monkeypatch) -> None:
    attributes, composer, window = _fake_accessibility(monkeypatch, value="Message Codex")
    button = object()
    attributes[button] = {
        "role": "AXButton", "position": (980.0, 760.0), "size": (36.0, 36.0),
        "title": "Send", "description": "", "help": "",
    }
    monkeypatch.setattr(macos_api, "ax_descendants", lambda _root: [composer, button])
    monkeypatch.setattr(macos_api, "activate_application", lambda _pid: True)
    monkeypatch.setattr(macos_api, "frontmost_pid", lambda: 321)
    monkeypatch.setattr(macos_api, "ax_set", lambda *_args: True)
    events = []
    @contextmanager
    def clipboard(_text):
        yield
    monkeypatch.setattr(macos_api, "temporary_clipboard_text", clipboard)
    monkeypatch.setattr(macos_api, "post_command_paste", lambda: events.append("paste"))
    monkeypatch.setattr(macos_api, "post_left_click", lambda x, y: events.append((x, y)))
    monkeypatch.setattr(macos_api, "post_return_to_pid", lambda pid: events.append(("targeted-return", pid)))
    monkeypatch.setattr(macos_ax.time, "sleep", lambda _seconds: None)
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    result = sender.send("普通消息", None)

    assert result.sent
    assert events == [(600, 765), "paste", ("targeted-return", 321)]
    assert "定向发送回车" in result.detail


def test_macos_targets_return_after_physical_composer_focus_when_send_button_is_missing(monkeypatch) -> None:
    _fake_accessibility(monkeypatch, value="Message Codex")
    monkeypatch.setattr(macos_api, "activate_application", lambda _pid: True)
    monkeypatch.setattr(macos_api, "frontmost_pid", lambda: 321)
    monkeypatch.setattr(macos_api, "ax_set", lambda *_args: True)
    events = []
    @contextmanager
    def clipboard(_text):
        yield
    monkeypatch.setattr(macos_api, "temporary_clipboard_text", clipboard)
    monkeypatch.setattr(macos_api, "post_left_click", lambda *_point: events.append("focus-click"))
    monkeypatch.setattr(macos_api, "post_command_paste", lambda: events.append("paste"))
    monkeypatch.setattr(macos_api, "post_return_to_pid", lambda _pid: events.append("targeted-return"))
    monkeypatch.setattr(macos_ax.time, "sleep", lambda _seconds: None)
    sender = macos_ax.MacOSCodexSender(CodexSettings())

    result = sender.send_voice("识别文字", None)

    assert result.sent
    assert events == ["focus-click", "paste", "targeted-return"]


def test_macos_paste_restores_clipboard_unless_user_changes_it(monkeypatch):
    class Item:
        def __init__(self):
            self.values = {}
        @classmethod
        def alloc(cls):
            return cls()
        def init(self):
            return self
        def types(self):
            return list(self.values)
        def dataForType_(self, kind):
            return self.values[kind]
        def setData_forType_(self, data, kind):
            self.values[kind] = data
            return True
        def setString_forType_(self, value, kind):
            self.values[kind] = value
            return True

    original = Item()
    original.values = {"public.utf8-plain-text": b"draft", "public.rtf": b"rich"}
    class Board:
        count = 1
        items = [original]
        def changeCount(self):
            return self.count
        def pasteboardItems(self):
            return self.items
        def writeObjects_(self, items):
            self.items = self.items + items
            self.count += 1
            return True
        def clearContents(self):
            self.items = []
            self.count += 1
            return self.count

    board = Board()
    appkit = SimpleNamespace(NSPasteboard=SimpleNamespace(generalPasteboard=lambda: board),
                             NSPasteboardItem=Item, NSPasteboardTypeString="public.utf8-plain-text")
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (appkit, None))
    with macos_api.temporary_clipboard_text("新语音"):
        assert board.items[0].values == {"public.utf8-plain-text": "新语音"}
    assert board.items[0].values == original.values

    with macos_api.temporary_clipboard_text("新语音"):
        board.clearContents()
        board.writeObjects_([Item()])  # Another application copied something.
    assert board.items[0].values == {}


@pytest.mark.parametrize("shortcut,key", [
    (macos_api.post_command_paste, 9),
    (macos_api.post_command_end, 125),
])
def test_macos_command_shortcut_releases_modifier(monkeypatch, shortcut, key):
    events = []
    def create(_source, virtual_key, down):
        return {"key": virtual_key, "down": down, "flags": 0}
    def set_flags(event, flags):
        event["flags"] = flags
    def post(_tap, event):
        events.append((event["key"], event["down"], event["flags"]))
    services = SimpleNamespace(
        CGEventCreateKeyboardEvent=create,
        CGEventSetFlags=set_flags,
        CGEventPost=post,
        kCGEventFlagMaskCommand=0x100,
        kCGHIDEventTap=1,
    )
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))

    shortcut()

    assert events == [
        (55, True, 0x100),
        (key, True, 0x100),
        (key, False, 0x100),
        (55, False, 0),
    ]


def test_macos_left_click_posts_mouse_down_and_up(monkeypatch):
    events = []
    services = SimpleNamespace(
        CGPoint=lambda x, y: (x, y),
        CGEventCreateMouseEvent=lambda _source, kind, point, button: (kind, point, button),
        CGEventPost=lambda _tap, event: events.append(event),
        kCGEventLeftMouseDown=1,
        kCGEventLeftMouseUp=2,
        kCGMouseButtonLeft=0,
        kCGHIDEventTap=3,
    )
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))

    macos_api.post_left_click(998, 778)

    assert events == [(1, (998.0, 778.0), 0), (2, (998.0, 778.0), 0)]


def test_macos_return_is_posted_to_codex_pid(monkeypatch):
    events = []
    application = object()
    services = SimpleNamespace(
        AXUIElementCreateApplication=lambda pid: events.append(("application", pid)) or application,
        AXUIElementPostKeyboardEvent=lambda target, char, key, down: (
            events.append((target, char, key, down)) or 0),
        kAXErrorSuccess=0,
    )
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))

    macos_api.post_return_to_pid(321)

    assert events == [
        ("application", 321),
        (application, 13, 36, True),
        (application, 13, 36, False),
    ]


def test_ax_append_text_selects_utf16_end_without_touching_clipboard(monkeypatch):
    element = object()
    values = {"value": "草稿😀"}
    selections = []
    services = SimpleNamespace(
        kAXValueCFRangeType=4,
        CFRange=lambda start, length: (start, length),
        AXValueCreate=lambda _kind, value: value,
        kAXSelectedTextRangeAttribute="selection",
        kAXSelectedTextAttribute="selected_text",
        kAXValueAttribute="value",
    )
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))
    monkeypatch.setattr(macos_api, "ax_copy", lambda _element, attr: values.get(attr))

    def set_attribute(_element, attr, value):
        if attr == "selection":
            selections.append(value)
        elif attr == "selected_text":
            values["value"] += value
        else:
            pytest.fail("replaced the entire draft")
        return True

    monkeypatch.setattr(macos_api, "ax_set", set_attribute)
    assert macos_api.ax_append_text(element, "草稿😀", "新语音")
    assert selections == [(4, 0)]
    assert values["value"] == "草稿😀新语音"


def test_ax_append_falls_back_to_verified_full_value(monkeypatch):
    element = object()
    values = {"value": "已有草稿"}
    services = SimpleNamespace(
        kAXValueCFRangeType=4,
        CFRange=lambda start, length: (start, length),
        AXValueCreate=lambda _kind, value: value,
        kAXSelectedTextRangeAttribute="selection",
        kAXSelectedTextAttribute="selected_text",
        kAXValueAttribute="value",
    )
    monkeypatch.setattr(macos_api, "_frameworks", lambda: (None, services))
    monkeypatch.setattr(macos_api, "ax_copy", lambda _element, attr: values.get(attr))

    def set_attribute(_element, attr, value):
        if attr == "selection":
            return False
        values[attr] = value
        return True

    monkeypatch.setattr(macos_api, "ax_set", set_attribute)
    assert macos_api.ax_append_text(element, "已有草稿", "新语音")
    assert values["value"] == "已有草稿新语音"


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
