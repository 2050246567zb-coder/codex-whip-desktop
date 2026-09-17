from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import Any, Iterable


class MacOSAPIError(RuntimeError):
    pass


_active_sounds: list[Any] = []
_sound_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class MacWindow:
    pid: int
    title: str
    left: int
    top: int
    width: int
    height: int
    element: Any


def _frameworks() -> tuple[Any, Any]:
    if sys.platform != "darwin":
        raise MacOSAPIError("macOS API requested outside macOS")
    try:
        import AppKit
        import Quartz
    except ImportError as exc:
        raise MacOSAPIError("Mac 版缺少 PyObjC/AppKit 运行时") from exc
    return AppKit, Quartz


def accessibility_trusted(*, prompt: bool = False) -> bool:
    _appkit, quartz = _frameworks()
    if prompt:
        return bool(
            quartz.AXIsProcessTrustedWithOptions(
                {quartz.kAXTrustedCheckOptionPrompt: True}
            )
        )
    return bool(quartz.AXIsProcessTrusted())


def ax_copy(element: Any, attribute: str) -> Any | None:
    _appkit, quartz = _frameworks()
    result = quartz.AXUIElementCopyAttributeValue(element, attribute, None)
    if isinstance(result, tuple) and len(result) == 2:
        error, value = result
        return value if int(error) == int(quartz.kAXErrorSuccess) else None
    return None


def ax_set(element: Any, attribute: str, value: Any) -> bool:
    _appkit, quartz = _frameworks()
    return int(quartz.AXUIElementSetAttributeValue(element, attribute, value)) == int(
        quartz.kAXErrorSuccess
    )


def _ax_point(value: Any) -> tuple[float, float] | None:
    _appkit, quartz = _frameworks()
    if value is None:
        return None
    result = quartz.AXValueGetValue(value, quartz.kAXValueCGPointType, None)
    if isinstance(result, tuple) and len(result) == 2 and result[0]:
        point = result[1]
        return float(point.x), float(point.y)
    return None


def _ax_size(value: Any) -> tuple[float, float] | None:
    _appkit, quartz = _frameworks()
    if value is None:
        return None
    result = quartz.AXValueGetValue(value, quartz.kAXValueCGSizeType, None)
    if isinstance(result, tuple) and len(result) == 2 and result[0]:
        size = result[1]
        return float(size.width), float(size.height)
    return None


def _candidate_applications() -> Iterable[Any]:
    appkit, _quartz = _frameworks()
    for application in appkit.NSWorkspace.sharedWorkspace().runningApplications():
        name = str(application.localizedName() or "").casefold()
        bundle = str(application.bundleIdentifier() or "").casefold()
        if is_official_codex_identity(name, bundle):
            yield application


def is_official_codex_identity(name: str, bundle_identifier: str) -> bool:
    """Reject unrelated apps that happen to use the ChatGPT or Codex name."""

    return (
        str(name).strip().casefold() in {"chatgpt", "codex"}
        and "openai" in str(bundle_identifier).strip().casefold()
    )


def _windows_for_pid(pid: int) -> list[Any]:
    _appkit, quartz = _frameworks()
    application = quartz.AXUIElementCreateApplication(int(pid))
    value = ax_copy(application, quartz.kAXWindowsAttribute)
    return list(value or ())


def _window_from_element(pid: int, element: Any) -> MacWindow | None:
    _appkit, quartz = _frameworks()
    if bool(ax_copy(element, quartz.kAXMinimizedAttribute)):
        return None
    position = _ax_point(ax_copy(element, quartz.kAXPositionAttribute))
    size = _ax_size(ax_copy(element, quartz.kAXSizeAttribute))
    if position is None or size is None or size[0] < 160 or size[1] < 120:
        return None
    title = str(ax_copy(element, quartz.kAXTitleAttribute) or "").strip()
    return MacWindow(
        int(pid),
        title,
        round(position[0]),
        round(position[1]),
        max(1, round(size[0])),
        max(1, round(size[1])),
        element,
    )


def codex_windows() -> list[MacWindow]:
    windows: list[MacWindow] = []
    for application in _candidate_applications():
        pid = int(application.processIdentifier())
        for element in _windows_for_pid(pid):
            value = _window_from_element(pid, element)
            if value is not None:
                windows.append(value)
    return windows


def window_for_pid(pid: int) -> MacWindow | None:
    values = [
        value
        for element in _windows_for_pid(pid)
        if (value := _window_from_element(pid, element)) is not None
    ]
    if len(values) == 1:
        return values[0]
    _appkit, quartz = _frameworks()
    focused = ax_copy(
        quartz.AXUIElementCreateApplication(int(pid)),
        quartz.kAXFocusedWindowAttribute,
    )
    if focused is not None:
        selected = _window_from_element(pid, focused)
        if selected is not None:
            return selected
    return values[0] if values else None


def window_is_available(pid: int) -> bool:
    return window_for_pid(pid) is not None


def window_is_fullscreen(pid: int) -> bool:
    _appkit, quartz = _frameworks()
    window = window_for_pid(pid)
    if window is None:
        return False
    return bool(ax_copy(window.element, "AXFullScreen"))


def move_window(pid: int, left: int, top: int) -> bool:
    _appkit, quartz = _frameworks()
    window = window_for_pid(pid)
    if window is None:
        return False
    point = quartz.CGPoint(float(left), float(top))
    wrapped = quartz.AXValueCreate(quartz.kAXValueCGPointType, point)
    return ax_set(window.element, quartz.kAXPositionAttribute, wrapped)


def cursor_position() -> tuple[int, int]:
    _appkit, quartz = _frameworks()
    event = quartz.CGEventCreate(None)
    point = quartz.CGEventGetLocation(event)
    return round(point.x), round(point.y)


def virtual_screen_rectangle() -> tuple[int, int, int, int]:
    appkit, _quartz = _frameworks()
    screens = list(appkit.NSScreen.screens())
    if not screens:
        return 0, 0, 1, 1
    main_height = float(appkit.NSScreen.mainScreen().frame().size.height)
    rectangles: list[tuple[float, float, float, float]] = []
    for screen in screens:
        frame = screen.frame()
        left = float(frame.origin.x)
        top = main_height - float(frame.origin.y + frame.size.height)
        rectangles.append(
            (left, top, left + float(frame.size.width), top + float(frame.size.height))
        )
    return (
        round(min(item[0] for item in rectangles)),
        round(min(item[1] for item in rectangles)),
        round(max(item[2] for item in rectangles)),
        round(max(item[3] for item in rectangles)),
    )


def animations_enabled() -> bool:
    appkit, _quartz = _frameworks()
    workspace = appkit.NSWorkspace.sharedWorkspace()
    try:
        return not bool(workspace.accessibilityDisplayShouldReduceMotion())
    except Exception:
        return True


def frontmost_pid() -> int | None:
    appkit, _quartz = _frameworks()
    application = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(application.processIdentifier()) if application is not None else None


def activate_application(pid: int) -> bool:
    appkit, _quartz = _frameworks()
    application = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(
        int(pid)
    )
    if application is None:
        return False
    options = getattr(appkit, "NSApplicationActivateIgnoringOtherApps", 1 << 1)
    return bool(application.activateWithOptions_(options))


def post_unicode_text(text: str) -> None:
    _appkit, quartz = _frameworks()
    for chunk_start in range(0, len(text), 32):
        chunk = text[chunk_start : chunk_start + 32]
        down = quartz.CGEventCreateKeyboardEvent(None, 0, True)
        quartz.CGEventKeyboardSetUnicodeString(down, len(chunk), chunk)
        quartz.CGEventPost(quartz.kCGHIDEventTap, down)
        up = quartz.CGEventCreateKeyboardEvent(None, 0, False)
        quartz.CGEventKeyboardSetUnicodeString(up, len(chunk), chunk)
        quartz.CGEventPost(quartz.kCGHIDEventTap, up)


def post_return() -> None:
    _appkit, quartz = _frameworks()
    for pressed in (True, False):
        event = quartz.CGEventCreateKeyboardEvent(None, 36, pressed)
        quartz.CGEventPost(quartz.kCGHIDEventTap, event)


def escape_pressed() -> bool:
    _appkit, quartz = _frameworks()
    return bool(
        quartz.CGEventSourceKeyState(
            quartz.kCGEventSourceStateCombinedSessionState,
            53,
        )
    )


def ax_descendants(root: Any, *, maximum: int = 1800) -> list[Any]:
    _appkit, quartz = _frameworks()
    pending = [root]
    result: list[Any] = []
    while pending and len(result) < maximum:
        current = pending.pop(0)
        children = ax_copy(current, quartz.kAXChildrenAttribute)
        for child in list(children or ()):
            result.append(child)
            pending.append(child)
            if len(result) >= maximum:
                break
    return result


def configure_tk_window(
    title: str,
    *,
    click_through: bool,
    transparent: bool,
    alpha: float = 1.0,
) -> bool:
    appkit, _quartz = _frameworks()
    for window in appkit.NSApp.windows():
        if str(window.title() or "") != title:
            continue
        if transparent:
            window.setOpaque_(False)
            window.setBackgroundColor_(appkit.NSColor.clearColor())
            window.setHasShadow_(False)
        window.setAlphaValue_(float(alpha))
        window.setIgnoresMouseEvents_(bool(click_through))
        level = getattr(appkit, "NSStatusWindowLevel", 25)
        window.setLevel_(level)
        behavior = (
            getattr(appkit, "NSWindowCollectionBehaviorCanJoinAllSpaces", 1 << 0)
            | getattr(appkit, "NSWindowCollectionBehaviorStationary", 1 << 4)
            | getattr(appkit, "NSWindowCollectionBehaviorFullScreenAuxiliary", 1 << 8)
        )
        window.setCollectionBehavior_(behavior)
        window.orderFrontRegardless()
        return True
    return False


def raise_tk_window(title: str) -> bool:
    appkit, _quartz = _frameworks()
    for window in appkit.NSApp.windows():
        if str(window.title() or "") == title:
            window.orderFrontRegardless()
            return True
    return False


def beep(kind: str = "default") -> bool:
    del kind
    appkit, _quartz = _frameworks()
    appkit.NSBeep()
    return True


def play_wav(data: bytes) -> bool:
    appkit, _quartz = _frameworks()
    try:
        import Foundation

        payload = Foundation.NSData.dataWithBytes_length_(data, len(data))
        sound = appkit.NSSound.alloc().initWithData_(payload)
        if sound is None:
            return False
        with _sound_lock:
            _active_sounds[:] = [item for item in _active_sounds if item.isPlaying()]
            _active_sounds.append(sound)
        return bool(sound.play())
    except Exception:
        return False
