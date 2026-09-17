from __future__ import annotations

import sys
from typing import Any

from ..settings import CodexSettings


def create_live_sender(
    settings: CodexSettings,
    *,
    prompt_permission: bool = True,
) -> Any:
    if sys.platform == "win32":
        from .windows_uia import WindowsCodexSender

        return WindowsCodexSender(settings)
    if sys.platform == "darwin":
        from .macos_ax import MacOSCodexSender

        return MacOSCodexSender(settings, prompt_permission=prompt_permission)
    raise RuntimeError(f"当前系统暂不支持控制 Codex：{sys.platform}")
