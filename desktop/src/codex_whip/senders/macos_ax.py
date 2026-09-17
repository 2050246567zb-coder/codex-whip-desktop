from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from typing import Any

from .. import macos_api
from ..models import WhipEvent
from ..settings import CodexSettings
from .base import SendResult


class CodexTargetError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _Candidate:
    element: Any
    role: str
    name: str
    value: str | None
    placeholder: str
    left: float
    top: float
    width: float
    height: float


class MacOSCodexSender:
    """Fail-closed Accessibility adapter for the visible macOS Codex window."""

    def __init__(self, settings: CodexSettings, *, prompt_permission: bool = True) -> None:
        if sys.platform != "darwin":
            raise CodexTargetError("Mac 发送器只能在 macOS 上运行")
        self._settings = settings
        if not macos_api.accessibility_trusted(prompt=prompt_permission):
            raise CodexTargetError(
                "需要在 系统设置 > 隐私与安全性 > 辅助功能 中允许 Codex Whip"
            )

    def _single_window(self) -> macos_api.MacWindow:
        windows = macos_api.codex_windows()
        if len(windows) != 1:
            raise CodexTargetError(
                f"需要且只能有一个可见 Codex 窗口，当前找到 {len(windows)} 个"
            )
        return windows[0]

    @staticmethod
    def _frame(element: Any) -> tuple[float, float, float, float] | None:
        _appkit, quartz = macos_api._frameworks()
        position = macos_api._ax_point(
            macos_api.ax_copy(element, quartz.kAXPositionAttribute)
        )
        size = macos_api._ax_size(
            macos_api.ax_copy(element, quartz.kAXSizeAttribute)
        )
        if position is None or size is None:
            return None
        return position[0], position[1], size[0], size[1]

    def _candidate(self, element: Any) -> _Candidate | None:
        _appkit, quartz = macos_api._frameworks()
        role = str(macos_api.ax_copy(element, quartz.kAXRoleAttribute) or "")
        if role not in {"AXTextArea", "AXTextField"}:
            return None
        frame = self._frame(element)
        if frame is None:
            return None
        title = str(macos_api.ax_copy(element, quartz.kAXTitleAttribute) or "")
        description = str(
            macos_api.ax_copy(element, quartz.kAXDescriptionAttribute) or ""
        )
        help_text = str(macos_api.ax_copy(element, quartz.kAXHelpAttribute) or "")
        name = " ".join(value for value in (title, description, help_text) if value)
        raw_value = macos_api.ax_copy(element, quartz.kAXValueAttribute)
        value = None if raw_value is None else str(raw_value).strip()
        placeholder_attribute = getattr(
            quartz,
            "kAXPlaceholderValueAttribute",
            "AXPlaceholderValue",
        )
        placeholder = str(
            macos_api.ax_copy(element, placeholder_attribute) or ""
        ).strip()
        return _Candidate(element, role, name, value, placeholder, *frame)

    def _composer(self, window: macos_api.MacWindow) -> _Candidate:
        candidates: list[tuple[float, _Candidate]] = []
        for element in macos_api.ax_descendants(window.element):
            candidate = self._candidate(element)
            if candidate is None:
                continue
            if candidate.width < max(240, window.width * 0.25):
                continue
            if candidate.height < 24 or candidate.height > max(280, window.height * 0.38):
                continue
            center_y = candidate.top + candidate.height / 2
            if center_y < window.top + window.height * 0.52:
                continue
            normalized = candidate.name.casefold()
            score = (candidate.top + candidate.height - window.top) / max(window.height, 1) * 100
            if any(hint.casefold() in normalized for hint in self._settings.composer_name_hints):
                score += 250
            if candidate.role == "AXTextArea":
                score += 100
            candidates.append((score, candidate))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if not candidates:
            raise CodexTargetError("无法识别 Codex 消息输入框")
        if len(candidates) > 1 and abs(candidates[0][0] - candidates[1][0]) < 1:
            raise CodexTargetError("Codex 消息输入框不唯一，已拒绝发送")
        return candidates[0][1]

    @staticmethod
    def _normalized_value(candidate: _Candidate) -> str | None:
        value = candidate.value
        if value is None:
            return None
        placeholders = (candidate.placeholder, candidate.name.strip())
        if value and any(
            placeholder and value.casefold() == placeholder.casefold()
            for placeholder in placeholders
        ):
            return ""
        return value

    def locate_window(self) -> dict[str, object]:
        window = self._single_window()
        return {
            "title": window.title,
            "pid": window.pid,
            "handle": window.pid,
            "rectangle": {
                "left": window.left,
                "top": window.top,
                "right": window.left + window.width,
                "bottom": window.top + window.height,
            },
        }

    def diagnose(self) -> list[dict[str, object]]:
        return [
            {
                "title": window.title,
                "pid": window.pid,
                "handle": window.pid,
                "rectangle": {
                    "left": window.left,
                    "top": window.top,
                    "right": window.left + window.width,
                    "bottom": window.top + window.height,
                },
            }
            for window in macos_api.codex_windows()
        ]

    def check_ready(self) -> dict[str, object]:
        window = self._single_window()
        composer = self._composer(window)
        existing = self._normalized_value(composer)
        if self._settings.refuse_when_composer_has_text:
            if existing is None:
                raise CodexTargetError("无法确认 Codex 输入框是否为空")
            if existing:
                raise CodexTargetError("Codex 输入框已有未发送草稿")
        return {
            "title": window.title,
            "pid": window.pid,
            "handle": window.pid,
            "composer_empty": existing == "",
        }

    def _send_button(self, window: macos_api.MacWindow) -> Any | None:
        _appkit, quartz = macos_api._frameworks()
        matches: list[Any] = []
        for element in macos_api.ax_descendants(window.element):
            role = str(macos_api.ax_copy(element, quartz.kAXRoleAttribute) or "")
            if role != "AXButton":
                continue
            name = " ".join(
                str(macos_api.ax_copy(element, attribute) or "")
                for attribute in (
                    quartz.kAXTitleAttribute,
                    quartz.kAXDescriptionAttribute,
                    quartz.kAXHelpAttribute,
                )
            ).casefold()
            frame = self._frame(element)
            if frame is None or frame[1] < window.top + window.height * 0.52:
                continue
            if any(hint.casefold() in name for hint in self._settings.send_button_name_hints):
                matches.append(element)
        return matches[0] if len(matches) == 1 else None

    def send(self, prompt: str, event: WhipEvent) -> SendResult:
        del event
        window = self._single_window()
        composer = self._composer(window)
        existing = self._normalized_value(composer)
        if self._settings.refuse_when_composer_has_text:
            if existing is None:
                raise CodexTargetError("无法确认 Codex 输入框是否为空")
            if existing:
                raise CodexTargetError("Codex 输入框已有未发送草稿")
        _appkit, quartz = macos_api._frameworks()
        if not macos_api.activate_application(window.pid):
            raise CodexTargetError("macOS 拒绝激活 Codex 窗口")
        time.sleep(0.16)
        if not macos_api.ax_set(composer.element, quartz.kAXFocusedAttribute, True):
            raise CodexTargetError("无法聚焦 Codex 输入框")
        time.sleep(0.08)
        if macos_api.frontmost_pid() != window.pid:
            raise CodexTargetError("输入前 Codex 已失去前台焦点")
        macos_api.post_unicode_text(prompt)
        time.sleep(0.06)
        if macos_api.frontmost_pid() != window.pid:
            raise CodexTargetError("提交前 Codex 已失去前台焦点")
        inserted = macos_api.ax_copy(composer.element, quartz.kAXValueAttribute)
        if inserted is None or str(inserted) != prompt:
            raise CodexTargetError("Codex 输入内容未能验证，已拒绝提交")
        button = self._send_button(window)
        if button is not None:
            error = quartz.AXUIElementPerformAction(button, quartz.kAXPressAction)
            if int(error) != int(quartz.kAXErrorSuccess):
                macos_api.post_return()
        else:
            macos_api.post_return()
        return SendResult(True, "消息已提交到 macOS Codex 窗口")
