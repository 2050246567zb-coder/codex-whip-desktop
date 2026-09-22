from __future__ import annotations

import sys
from typing import Any

from ..settings import CodexSettings


def create_live_sender(
    settings: CodexSettings,
    *,
    prompt_permission: bool = False,
) -> Any:
    # Construction also runs during background window polling. Never request
    # system permissions unless a caller explicitly opts in.
    if sys.platform == "win32":
        from .windows_uia import WindowsCodexSender

        return WindowsCodexSender(settings)
    if sys.platform == "darwin":
        if settings.target_app != 'Codex':
            raise RuntimeError('此版本的 Claude 桌面适配仅支持 Windows；不会改为发送给 Codex')
        from .macos_ax import MacOSCodexSender

        return MacOSCodexSender(settings, prompt_permission=prompt_permission)
    raise RuntimeError(f"当前系统暂不支持控制 Codex：{sys.platform}")
