from __future__ import annotations

import ctypes
import os
import sys
import threading
from dataclasses import dataclass
from typing import Callable

if os.name == "nt":
    from ctypes import wintypes


MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0x0000


class HotkeyRegistrationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedHotkey:
    canonical: str
    modifiers: int
    virtual_key: int


_MODIFIERS = {
    "ctrl": ("ctrl", MOD_CONTROL),
    "control": ("ctrl", MOD_CONTROL),
    "alt": ("alt", MOD_ALT),
    "shift": ("shift", MOD_SHIFT),
    "win": ("win", MOD_WIN),
    "windows": ("win", MOD_WIN),
    "cmd": ("win", MOD_WIN),
    "command": ("win", MOD_WIN),
}
_MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")


def parse_hotkey(value: str) -> ParsedHotkey:
    tokens = [part.strip().lower() for part in str(value).split("+") if part.strip()]
    if not tokens:
        raise ValueError("组合键不能为空")
    names: set[str] = set()
    modifiers = 0
    keys: list[str] = []
    for token in tokens:
        modifier = _MODIFIERS.get(token)
        if modifier is None:
            keys.append(token)
            continue
        name, flag = modifier
        names.add(name)
        modifiers |= flag
    if not names:
        raise ValueError("组合键至少要包含 Ctrl、Alt、Shift 或 Win 中的一个")
    if len(keys) != 1:
        raise ValueError("组合键必须且只能包含一个普通按键")
    key = keys[0]
    if len(key) == 1 and ("a" <= key <= "z" or "0" <= key <= "9"):
        virtual_key = ord(key.upper())
        display_key = key
    elif key.startswith("f") and key[1:].isdigit() and 1 <= int(key[1:]) <= 12:
        virtual_key = 0x70 + int(key[1:]) - 1
        display_key = key
    else:
        raise ValueError("普通按键仅支持 A–Z、0–9 或 F1–F12")
    canonical = "+".join([name for name in _MODIFIER_ORDER if name in names] + [display_key])
    return ParsedHotkey(canonical, modifiers | MOD_NOREPEAT, virtual_key)


class GlobalHotkey:
    """System-wide hotkey listener for Win32 and macOS."""

    _HOTKEY_ID = 0xC0DE

    def __init__(self, hotkey: str, callback: Callable[[], None]) -> None:
        self.parsed = parse_hotkey(hotkey)
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._mac_global_monitor: object | None = None
        self._mac_local_monitor: object | None = None

    @property
    def canonical(self) -> str:
        return self.parsed.canonical

    def start(self) -> None:
        if sys.platform == "darwin":
            self._start_macos()
            return
        if os.name != "nt":
            raise HotkeyRegistrationError("当前系统不支持全局组合键")
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run,
            name="codex-whip-hotkey",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(2.0):
            raise HotkeyRegistrationError("启动全局组合键监听超时")
        if self._error is not None:
            raise HotkeyRegistrationError(str(self._error)) from self._error

    def _mac_matches(self, event: object) -> bool:
        import AppKit

        if bool(event.isARepeat()):
            return False
        required = 0
        if self.parsed.modifiers & MOD_CONTROL:
            required |= int(AppKit.NSEventModifierFlagControl)
        if self.parsed.modifiers & MOD_ALT:
            required |= int(AppKit.NSEventModifierFlagOption)
        if self.parsed.modifiers & MOD_SHIFT:
            required |= int(AppKit.NSEventModifierFlagShift)
        if self.parsed.modifiers & MOD_WIN:
            required |= int(AppKit.NSEventModifierFlagCommand)
        device_mask = int(AppKit.NSEventModifierFlagDeviceIndependentFlagsMask)
        if int(event.modifierFlags()) & device_mask != required:
            return False
        virtual_key = self.parsed.virtual_key
        if ord("0") <= virtual_key <= ord("9") or ord("A") <= virtual_key <= ord("Z"):
            value = str(event.charactersIgnoringModifiers() or "").casefold()
            return value == chr(virtual_key).casefold()
        function_key_codes = (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111)
        index = virtual_key - 0x70
        return 0 <= index < len(function_key_codes) and int(event.keyCode()) == function_key_codes[index]

    def _start_macos(self) -> None:
        if self._mac_global_monitor is not None:
            return
        try:
            import AppKit

            from .macos_api import accessibility_trusted

            if not accessibility_trusted(prompt=False):
                raise HotkeyRegistrationError(
                    "需要在 系统设置 > 隐私与安全性 > 辅助功能 中允许 Codex Whip"
                )

            def global_handler(event: object) -> None:
                if self._mac_matches(event):
                    try:
                        self._callback()
                    except Exception:
                        pass

            def local_handler(event: object) -> object:
                global_handler(event)
                return event

            mask = AppKit.NSEventMaskKeyDown
            self._mac_global_monitor = (
                AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    mask, global_handler
                )
            )
            self._mac_local_monitor = (
                AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
                    mask, local_handler
                )
            )
            if self._mac_global_monitor is None:
                raise HotkeyRegistrationError(f"无法注册组合键 {self.canonical}")
        except HotkeyRegistrationError:
            raise
        except Exception as exc:
            raise HotkeyRegistrationError(f"注册组合键失败：{exc}") from exc

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        message = wintypes.MSG()
        self._thread_id = threading.get_native_id()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)
        registered = bool(
            user32.RegisterHotKey(
                None,
                self._HOTKEY_ID,
                self.parsed.modifiers,
                self.parsed.virtual_key,
            )
        )
        if not registered:
            self._error = HotkeyRegistrationError(
                f"组合键 {self.canonical} 已被其他程序占用或无法注册"
            )
            self._ready.set()
            return
        self._ready.set()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == self._HOTKEY_ID:
                    try:
                        self._callback()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, self._HOTKEY_ID)

    def stop(self) -> None:
        if sys.platform == "darwin":
            try:
                import AppKit

                for monitor in (self._mac_global_monitor, self._mac_local_monitor):
                    if monitor is not None:
                        AppKit.NSEvent.removeMonitor_(monitor)
            finally:
                self._mac_global_monitor = None
                self._mac_local_monitor = None
            return
        thread = self._thread
        thread_id = self._thread_id
        if thread is None:
            return
        if os.name == "nt" and thread.is_alive() and thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = None
