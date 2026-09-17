from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY_NAME = "CodexWhip"


def user_data_dir() -> Path:
    """Return the per-user data directory on Windows, macOS, or a dev shell.

    ``CODEX_WHIP_DATA_DIR`` is intentionally supported for migration tests and
    portable builds.  The normal macOS location follows Apple's Application
    Support convention and keeps every learned JSON profile together with the
    local speech runtime.
    """

    override = os.environ.get("CODEX_WHIP_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIRECTORY_NAME
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_DIRECTORY_NAME
    return Path.cwd() / f".{APP_DIRECTORY_NAME.lower()}"


def voice_runtime_dir() -> Path:
    """Return the writable directory containing Whisper models and recordings."""

    if sys.platform == "darwin":
        return user_data_dir() / "voice"
    program_data = os.environ.get("PROGRAMDATA")
    if program_data:
        return Path(program_data) / APP_DIRECTORY_NAME / "voice"
    return user_data_dir() / "voice"
