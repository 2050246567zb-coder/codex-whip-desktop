from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psutil

from ..models import WhipEvent
from ..settings import CodexSettings
from .base import SendResult


class CodexTargetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Rectangle:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True, slots=True)
class ControlCandidate:
    control_type: str
    name: str
    rectangle: Rectangle
    enabled: bool = True
    visible: bool = True
    class_name: str = ""


def score_composer_candidate(
    candidate: ControlCandidate, window: Rectangle, name_hints: tuple[str, ...],
    *, target_app: str = 'Codex',
) -> float | None:
    if not candidate.enabled or not candidate.visible:
        return None
    if candidate.control_type not in {"Edit", "Document"}:
        return None
    claude_editor = (target_app == 'Claude' and candidate.control_type == 'Edit'
                     and candidate.name.casefold() == 'write your prompt to claude')
    if candidate.rectangle.width < max(240, int(window.width * 0.25)):
        return None
    if target_app == 'Claude' and not claude_editor:
        return None
    if candidate.rectangle.height < 24:
        return None
    if candidate.rectangle.height > max(260, int(window.height * 0.35)):
        return None

    center_y = (candidate.rectangle.top + candidate.rectangle.bottom) / 2
    if center_y < window.top + window.height * (0.15 if claude_editor else 0.55):
        return None

    normalized_name = candidate.name.casefold()
    hint_bonus = 250 if any(hint.casefold() in normalized_name for hint in name_hints) else 0
    type_bonus = 100 if candidate.control_type == "Edit" else 0
    editor_bonus = 300 if "prosemirror" in candidate.class_name.casefold() else 0
    bottom_score = (candidate.rectangle.bottom - window.top) / max(window.height, 1) * 100
    return hint_bonus + type_bonus + editor_bonus + bottom_score


def normalize_composer_value(value: str, name: str, class_name: str) -> str:
    """Return actual draft text, excluding Codex's empty-editor placeholder.

    The packaged Codex app currently exposes its empty ProseMirror editor through
    UI Automation with the placeholder (for example, ``随心输入``) as both the
    accessible name and value. Treating that value as a draft would make the
    fail-closed check reject every empty composer.
    """
    normalized_value = value.strip()
    normalized_name = name.strip()
    if (
        "prosemirror" in class_name.casefold()
        and normalized_name
        and normalized_value == normalized_name
    ):
        return ""
    return normalized_value


def _rect(value: Any) -> Rectangle:
    return Rectangle(
        left=int(value.left),
        top=int(value.top),
        right=int(value.right),
        bottom=int(value.bottom),
    )


def composer_identity(control: Any, target_app: str) -> str:
    # UIA rich_text can contain placeholder/draft text. Claude's accessible
    # Name is the stable editor identity, independent of its contents.
    if target_app == 'Claude':
        return control.element_info.name or ''
    return control.window_text() or control.element_info.name or ''


def _is_codex_executable(executable: str, package_marker: str) -> bool:
    normalized = executable.casefold()
    return (
        Path(executable).name.casefold() == "chatgpt.exe"
        and package_marker.casefold() in normalized
    )


def is_target_executable(executable: str, settings: CodexSettings) -> bool:
    if settings.target_app == 'Claude':
        return Path(executable).name.casefold() == 'claude.exe'
    if settings.target_app != 'Codex':
        return False
    return _is_codex_executable(executable, settings.package_marker)


if sys.platform == "win32":
    ULONG_PTR = wintypes.WPARAM

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class INPUT_UNION(ctypes.Union):
        # MOUSEINPUT is the largest native union member. Omitting it makes
        # INPUT 32 bytes on x64 instead of the required 40 bytes.
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("union",)
        _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]

    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_RETURN = 0x0D
    SW_RESTORE = 9

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.SendInput.argtypes = (
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    )
    _user32.SendInput.restype = wintypes.UINT
    _user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
    _user32.SetForegroundWindow.restype = wintypes.BOOL
    _user32.GetForegroundWindow.argtypes = ()
    _user32.GetForegroundWindow.restype = wintypes.HWND


def _send_key(vk: int, scan: int = 0, flags: int = 0) -> None:
    if sys.platform != "win32":
        raise CodexTargetError("live sender requires Windows")
    events = (INPUT * 2)(
        INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(vk, scan, flags, 0, 0)),
        INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(vk, scan, flags | KEYEVENTF_KEYUP, 0, 0),
        ),
    )
    ctypes.set_last_error(0)
    sent = _user32.SendInput(2, events, ctypes.sizeof(INPUT))
    if sent != 2:
        error_code = ctypes.get_last_error()
        error_text = ctypes.FormatError(error_code).strip() if error_code else "unknown"
        raise CodexTargetError(
            "Windows SendInput did not accept the complete key pair "
            f"(sent {sent}/2, WinError {error_code}: {error_text})"
        )


def _type_unicode(text: str) -> None:
    encoded = text.encode("utf-16-le")
    for index in range(0, len(encoded), 2):
        code_unit = encoded[index] | (encoded[index + 1] << 8)
        _send_key(0, code_unit, KEYEVENTF_UNICODE)


class WindowsCodexSender:
    """Fail-closed UI adapter for the packaged Codex desktop app.

    This module is intentionally not exercised against the real Codex window by
    automated tests. The user must opt into it with the CLI's --live flag.
    """

    def __init__(self, settings: CodexSettings) -> None:
        if os.name != "nt":
            raise CodexTargetError("live Codex window sending is Windows-only")
        self._settings = settings

    @staticmethod
    def _desktop() -> Any:
        try:
            from pywinauto import Desktop
        except ImportError as exc:
            raise CodexTargetError("pywinauto is required for live mode") from exc
        return Desktop(backend="uia")

    def _codex_windows(self) -> list[Any]:
        candidates: list[Any] = []
        for window in self._desktop().windows(visible_only=True):
            try:
                pid = window.process_id()
                executable = psutil.Process(pid).exe()
                title = window.window_text().strip()
                if title and is_target_executable(executable, self._settings):
                    candidates.append(window)
            except (psutil.Error, RuntimeError, OSError):
                continue
        return candidates

    def diagnose(self) -> list[dict[str, object]]:
        return [
            {
                "title": window.window_text(),
                "pid": window.process_id(),
                "handle": int(window.handle),
            }
            for window in self._codex_windows()
        ]

    def check_ready(self) -> dict[str, object]:
        """Validate the live target without focusing it or entering any text."""
        window = self._single_codex_window()
        composer = self._find_composer(window)
        existing = self._composer_value(composer)
        if self._settings.refuse_when_composer_has_text:
            if existing is None:
                raise CodexTargetError(
                    "could not verify that the Codex composer is empty"
                )
            if existing:
                raise CodexTargetError("Codex composer already contains unsent text")
        return {
            "title": window.window_text(),
            "pid": window.process_id(),
            "handle": int(window.handle),
            "composer_empty": existing == "",
        }

    def locate_window(self) -> dict[str, object]:
        """Return the unique Codex window without inspecting its composer."""
        window = self._single_codex_window()
        rectangle = _rect(window.rectangle())
        return {
            "title": window.window_text(),
            "pid": window.process_id(),
            "handle": int(window.handle),
            "rectangle": rectangle,
        }

    def _single_codex_window(self) -> Any:
        windows = self._codex_windows()
        if len(windows) != 1:
            raise CodexTargetError(
                f"expected exactly one visible {self._settings.target_app} window, found {len(windows)}"
            )
        return windows[0]

    def _find_composer(self, window: Any) -> Any:
        window_rect = _rect(window.rectangle())
        ranked: list[tuple[float, Any]] = []
        diagnostics: list[str] = []
        controls = window.descendants(control_type="Edit")
        controls += window.descendants(control_type="Document")
        for control in controls:
            try:
                candidate = ControlCandidate(
                    control_type=control.element_info.control_type,
                    name=composer_identity(control, self._settings.target_app),
                    rectangle=_rect(control.rectangle()),
                    enabled=control.is_enabled(),
                    visible=control.is_visible(),
                    class_name=control.element_info.class_name or "",
                )
                score = score_composer_candidate(
                    candidate, window_rect, self._settings.composer_name_hints,
                    target_app=self._settings.target_app,
                )
                if self._settings.target_app == 'Claude':
                    diagnostics.append(
                        f'{candidate.control_type}:{candidate.rectangle.width}x{candidate.rectangle.height}'
                        f'@{candidate.rectangle.top-window_rect.top}'
                        f',enabled={candidate.enabled},visible={candidate.visible}'
                        f',known={candidate.name.casefold() == "write your prompt to claude"}'
                    )
                if score is not None:
                    ranked.append((score, control))
            except (RuntimeError, OSError):
                continue

        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            detail = '; '.join(diagnostics[:8])
            raise CodexTargetError(f"could not identify the {self._settings.target_app} message composer"
                                   + (f' ({detail}; window={window_rect.width}x{window_rect.height})' if detail else ''))
        if len(ranked) > 1 and abs(ranked[0][0] - ranked[1][0]) < 1:
            raise CodexTargetError(f"{self._settings.target_app} message composer is ambiguous")
        return ranked[0][1]

    def _composer_value(self, composer: Any) -> str | None:
        try:
            value = composer.get_value()
            name = composer.window_text() or composer.element_info.name or ""
            class_name = composer.element_info.class_name or ""
        except (AttributeError, RuntimeError, OSError):
            return None
        if self._settings.target_app == 'Claude':
            return str(value or '').strip()
        return normalize_composer_value(str(value or ""), str(name), str(class_name))

    def _find_send_button(self, window: Any) -> Any | None:
        window_rect = _rect(window.rectangle())
        matches: list[Any] = []
        for button in window.descendants(control_type="Button"):
            try:
                name = (button.window_text() or button.element_info.name or "").casefold()
                rect = _rect(button.rectangle())
                if (
                    button.is_visible()
                    and button.is_enabled()
                    and rect.top >= window_rect.top + window_rect.height * 0.55
                    and any(hint.casefold() in name for hint in self._settings.send_button_name_hints)
                ):
                    matches.append(button)
            except (RuntimeError, OSError):
                continue
        return matches[0] if len(matches) == 1 else None

    def send(self, prompt: str, event: WhipEvent) -> SendResult:
        del event
        window = self._single_codex_window()
        composer = self._find_composer(window)
        existing = self._composer_value(composer)
        if self._settings.refuse_when_composer_has_text:
            if existing is None:
                raise CodexTargetError("could not verify that the Codex composer is empty")
            if existing:
                raise CodexTargetError("Codex composer already contains unsent text")

        hwnd = int(window.handle)
        _user32.ShowWindow(hwnd, SW_RESTORE)
        if not _user32.SetForegroundWindow(hwnd):
            raise CodexTargetError("Windows refused to foreground the Codex window")
        time.sleep(0.15)
        composer.set_focus()
        time.sleep(0.10)
        if _user32.GetForegroundWindow() != hwnd:
            raise CodexTargetError("Codex lost foreground focus before text entry")

        _type_unicode(prompt)
        time.sleep(0.05)
        if _user32.GetForegroundWindow() != hwnd:
            raise CodexTargetError("Codex lost foreground focus before submit")

        send_button = self._find_send_button(window)
        if send_button is not None:
            # UI Automation invocation does not synthesize a physical mouse click,
            # so it cannot leak through or be swallowed by the manual-whip shield.
            try:
                send_button.invoke()
            except (AttributeError, RuntimeError, OSError):
                _send_key(VK_RETURN)
        else:
            _send_key(VK_RETURN)
        return SendResult(sent=True, detail="prompt submitted to the Codex desktop window")
