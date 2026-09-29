from __future__ import annotations

import sys
import time
import logging
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


@dataclass(frozen=True, slots=True)
class MacDictationSession:
    pid: int
    element: Any


class MacOSCodexSender:
    """Fail-closed Accessibility adapter for the visible macOS Codex window."""

    def __init__(self, settings: CodexSettings, *, prompt_permission: bool = False) -> None:
        if sys.platform != "darwin":
            raise CodexTargetError("Mac 发送器只能在 macOS 上运行")
        self._settings = settings
        if not macos_api.accessibility_trusted(prompt=prompt_permission):
            raise CodexTargetError(
                "需要在 系统设置 > 隐私与安全性 > 设备控制和数据访问（辅助功能）中允许 Codex Whip"
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
        _appkit, services = macos_api._frameworks()
        position = macos_api._ax_point(
            macos_api.ax_copy(element, services.kAXPositionAttribute)
        )
        size = macos_api._ax_size(
            macos_api.ax_copy(element, services.kAXSizeAttribute)
        )
        if position is None or size is None:
            return None
        return position[0], position[1], size[0], size[1]

    def _candidate(self, element: Any) -> _Candidate | None:
        _appkit, services = macos_api._frameworks()
        role = str(macos_api.ax_copy(element, services.kAXRoleAttribute) or "")
        if role not in {"AXTextArea", "AXTextField"}:
            return None
        frame = self._frame(element)
        if frame is None:
            return None
        title = str(macos_api.ax_copy(element, services.kAXTitleAttribute) or "")
        description = str(
            macos_api.ax_copy(element, services.kAXDescriptionAttribute) or ""
        )
        help_text = str(macos_api.ax_copy(element, services.kAXHelpAttribute) or "")
        name = " ".join(value for value in (title, description, help_text) if value)
        raw_value = macos_api.ax_copy(element, services.kAXValueAttribute)
        # Keep the exact draft, including trailing spaces or newlines, so a
        # recognized phrase can be appended without changing existing text.
        value = None if raw_value is None else str(raw_value)
        placeholder_attribute = getattr(
            services,
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
            placeholder and value.strip().casefold() == placeholder.casefold()
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
        # A draft is safe to leave untouched while enabling the send switch.
        # Recognized speech may later append to it; ordinary prompts still
        # refuse to replace it.
        return {
            "title": window.title,
            "pid": window.pid,
            "handle": window.pid,
            "composer_empty": existing == "",
        }

    def _send_button(self, window: macos_api.MacWindow) -> Any | None:
        _appkit, services = macos_api._frameworks()
        matches: list[Any] = []
        for element in macos_api.ax_descendants(window.element):
            role = str(macos_api.ax_copy(element, services.kAXRoleAttribute) or "")
            if role != "AXButton":
                continue
            name = " ".join(
                str(macos_api.ax_copy(element, attribute) or "")
                for attribute in (
                    services.kAXTitleAttribute,
                    services.kAXDescriptionAttribute,
                    services.kAXHelpAttribute,
                )
            ).casefold()
            frame = self._frame(element)
            if frame is None or frame[1] < window.top + window.height * 0.52:
                continue
            if any(hint.casefold() in name for hint in self._settings.send_button_name_hints):
                matches.append(element)
        return matches[0] if len(matches) == 1 else None

    def _submit_composer(self, window: macos_api.MacWindow) -> str:
        """Submit once with Return addressed directly to the Codex process."""
        if macos_api.frontmost_pid() != window.pid:
            raise CodexTargetError("提交前 Codex 已失去前台焦点")
        macos_api.post_return_to_pid(window.pid)
        method = "targeted-return"
        logging.getLogger(__name__).info("Codex submit event posted via %s", method)
        return method

    def _dictation_button(self, window: macos_api.MacWindow) -> Any:
        _appkit, services = macos_api._frameworks()
        accepted = {"听写", "dictate", "dictation", "voice input", "语音输入"}
        matches: list[Any] = []
        for element in macos_api.ax_descendants(window.element):
            if str(macos_api.ax_copy(element, services.kAXRoleAttribute) or "") != "AXButton":
                continue
            name = " ".join(
                str(macos_api.ax_copy(element, attribute) or "")
                for attribute in (services.kAXTitleAttribute, services.kAXDescriptionAttribute,
                                  services.kAXHelpAttribute)
            ).strip().casefold()
            frame = self._frame(element)
            if frame is not None and frame[1] >= window.top + window.height * 0.52 and name in accepted:
                matches.append(element)
        if len(matches) != 1:
            raise CodexTargetError(f"需要唯一的 Codex 听写按钮，当前找到 {len(matches)} 个")
        return matches[0]

    @staticmethod
    def _press(element: Any, detail: str) -> None:
        _appkit, services = macos_api._frameworks()
        error = services.AXUIElementPerformAction(element, services.kAXPressAction)
        if int(error) != int(services.kAXErrorSuccess):
            raise CodexTargetError(detail)

    def start_dictation(self) -> MacDictationSession:
        window = self._single_window()
        composer = self._composer(window)
        existing = self._normalized_value(composer)
        if existing is None:
            raise CodexTargetError("无法确认 Codex 输入框是否为空")
        if existing:
            raise CodexTargetError("Codex 输入框已有未发送草稿")
        button = self._dictation_button(window)
        if not macos_api.activate_application(window.pid):
            raise CodexTargetError("macOS 拒绝激活 Codex 窗口")
        time.sleep(0.12)
        self._press(button, "无法启动 Codex 听写")
        return MacDictationSession(window.pid, button)

    def stop_dictation(self, session: MacDictationSession) -> None:
        if macos_api.window_for_pid(session.pid) is None:
            raise CodexTargetError("Codex 听写窗口已经关闭")
        # Reuse the exact AX element that opened dictation; never press an
        # unrelated generic Stop button.
        self._press(session.element, "无法安全停止 Codex 听写")

    def submit_existing(self, event: WhipEvent) -> SendResult:
        del event
        window = self._single_window()
        composer = self._composer(window)
        existing = self._normalized_value(composer)
        if existing is None:
            raise CodexTargetError("无法确认 Codex 听写草稿")
        if not existing:
            raise CodexTargetError("Codex 听写尚未生成可发送文字")
        _appkit, services = macos_api._frameworks()
        if not macos_api.activate_application(window.pid):
            raise CodexTargetError("macOS 拒绝激活 Codex 窗口")
        time.sleep(0.12)
        if not macos_api.ax_set(composer.element, services.kAXFocusedAttribute, True):
            raise CodexTargetError("无法聚焦 Codex 输入框")
        button = self._send_button(window)
        if button is not None:
            error = services.AXUIElementPerformAction(button, services.kAXPressAction)
            if int(error) != int(services.kAXErrorSuccess):
                macos_api.post_return()
        else:
            macos_api.post_return()
        return SendResult(True, "Codex 听写草稿已发送")

    def send(self, prompt: str, event: WhipEvent) -> SendResult:
        return self._send_text(prompt, event, append_to_draft=False)

    def send_voice(self, prompt: str, event: WhipEvent) -> SendResult:
        """Append recognized speech to a Codex draft, then submit both."""
        return self._send_text(prompt, event, append_to_draft=True)

    def _send_text(self, prompt: str, event: WhipEvent, *, append_to_draft: bool) -> SendResult:
        del event
        if not prompt:
            raise CodexTargetError("没有可发送的文字")
        window = self._single_window()
        composer = self._composer(window)
        existing = self._normalized_value(composer)
        if existing is None and (append_to_draft or self._settings.refuse_when_composer_has_text):
            raise CodexTargetError("无法确认 Codex 输入框是否为空")
        if existing and not append_to_draft and self._settings.refuse_when_composer_has_text:
            raise CodexTargetError("Codex 输入框已有未发送草稿")
        _appkit, services = macos_api._frameworks()
        if not macos_api.activate_application(window.pid):
            raise CodexTargetError("macOS 拒绝激活 Codex 窗口")
        time.sleep(0.16)
        if not macos_api.ax_set(composer.element, services.kAXFocusedAttribute, True):
            raise CodexTargetError("无法聚焦 Codex 输入框")
        time.sleep(0.08)
        if macos_api.frontmost_pid() != window.pid:
            raise CodexTargetError("输入前 Codex 已失去前台焦点")
        # AXFocused can report success while Codex still routes keyboard input
        # to a transient copy/paste menu. A physical left click establishes
        # editor focus before moving the caret or pasting.
        macos_api.post_left_click(
            composer.left + composer.width / 2,
            composer.top + composer.height / 2,
        )
        time.sleep(0.12)
        if macos_api.frontmost_pid() != window.pid:
            raise CodexTargetError("点击输入框后 Codex 已失去前台焦点")
        if append_to_draft and existing:
            macos_api.post_command_end()
            time.sleep(0.04)
            current = macos_api.ax_copy(composer.element, services.kAXValueAttribute)
            if current is None or str(current) != existing:
                raise CodexTargetError("Codex 草稿在输入前发生变化，已取消发送")
        input_attempted = False
        try:
            with macos_api.temporary_clipboard_text(prompt):
                input_attempted = True
                macos_api.post_command_paste()
                time.sleep(1.0)
                if macos_api.frontmost_pid() != window.pid:
                    raise CodexTargetError("粘贴后 Codex 已失去前台焦点")
                self._submit_composer(window)
                time.sleep(0.08)
        except Exception as exc:
            if input_attempted:
                logging.getLogger(__name__).warning(
                    "Codex submit stopped after input: %s: %s",
                    type(exc).__name__, str(exc)[:180],
                )
                return SendResult(
                    False,
                    f"文字可能已进入 Codex，但未能确认发送：{exc}；请核对输入框并手动发送",
                    text_may_be_inserted=True,
                )
            if isinstance(exc, macos_api.MacOSAPIError):
                raise CodexTargetError(str(exc)) from exc
            raise
        return SendResult(True, "已向 Codex 定向发送回车，请确认消息已发出", text_may_be_inserted=True)
