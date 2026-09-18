from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .paths import user_data_dir


MIN_STRIKES_PER_WOUND = 0
MAX_STRIKES_PER_WOUND = 100
MIN_SCARE_BLACKOUT_MS = 200
MAX_SCARE_BLACKOUT_MS = 5000
MIN_SCARE_EYES_MS = 300
MAX_SCARE_EYES_MS = 10000
VISUAL_SETTINGS_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class VisualSettings:
    strikes_per_wound: int = 1
    wounds_enabled: bool = True
    sound_enabled: bool = True
    scare_enabled: bool = True
    scare_hotkey: str = "ctrl+alt+shift+x"
    scare_blackout_ms: int = 2000
    scare_eyes_ms: int = 3000

    def validated(self) -> "VisualSettings":
        value = int(self.strikes_per_wound)
        if not MIN_STRIKES_PER_WOUND <= value <= MAX_STRIKES_PER_WOUND:
            raise ValueError(
                f"PCB 伤口间隔必须在 {MIN_STRIKES_PER_WOUND}–"
                f"{MAX_STRIKES_PER_WOUND} 次之间"
            )
        blackout_ms = int(self.scare_blackout_ms)
        if not MIN_SCARE_BLACKOUT_MS <= blackout_ms <= MAX_SCARE_BLACKOUT_MS:
            raise ValueError(
                f"黑屏时间必须在 {MIN_SCARE_BLACKOUT_MS}–"
                f"{MAX_SCARE_BLACKOUT_MS} 毫秒之间"
            )
        eyes_ms = int(self.scare_eyes_ms)
        if not MIN_SCARE_EYES_MS <= eyes_ms <= MAX_SCARE_EYES_MS:
            raise ValueError(
                f"红眼显示时间必须在 {MIN_SCARE_EYES_MS}–"
                f"{MAX_SCARE_EYES_MS} 毫秒之间"
            )
        hotkey = str(self.scare_hotkey).strip().lower()
        if not hotkey:
            raise ValueError("组合键不能为空")
        return VisualSettings(
            strikes_per_wound=value,
            wounds_enabled=bool(self.wounds_enabled),
            sound_enabled=bool(self.sound_enabled),
            scare_enabled=bool(self.scare_enabled),
            scare_hotkey=hotkey,
            scare_blackout_ms=blackout_ms,
            scare_eyes_ms=eyes_ms,
        )


def default_visual_settings_path() -> Path:
    return user_data_dir() / "visual-settings.json"


def load_visual_settings(path: Path) -> VisualSettings:
    fallback = VisualSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") not in (1, 2, 3):
            return fallback
        legacy_timing = data.get("schema_version") in (1, 2)
        return VisualSettings(
            strikes_per_wound=int(data.get("strikes_per_wound", 1)),
            wounds_enabled=bool(data.get('wounds_enabled', True)),
            sound_enabled=bool(data.get('sound_enabled', True)),
            scare_enabled=bool(data.get("scare_enabled", True)),
            scare_hotkey=str(data.get("scare_hotkey", "ctrl+alt+shift+x")),
            scare_blackout_ms=(
                2000 if legacy_timing else int(data.get("scare_blackout_ms", 2000))
            ),
            scare_eyes_ms=(
                3000 if legacy_timing else int(data.get("scare_eyes_ms", 3000))
            ),
        ).validated()
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return fallback


def save_visual_settings(path: Path, settings: VisualSettings) -> None:
    value = settings.validated()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "schema_version": VISUAL_SETTINGS_SCHEMA_VERSION,
                "strikes_per_wound": value.strikes_per_wound,
                "wounds_enabled": value.wounds_enabled,
                "sound_enabled": value.sound_enabled,
                "scare_enabled": value.scare_enabled,
                "scare_hotkey": value.scare_hotkey,
                "scare_blackout_ms": value.scare_blackout_ms,
                "scare_eyes_ms": value.scare_eyes_ms,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class VisualSettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_visual_settings_path()
        self._settings = load_visual_settings(self.path)

    @property
    def settings(self) -> VisualSettings:
        return self._settings

    def update(self, settings: VisualSettings) -> None:
        value = settings.validated()
        save_visual_settings(self.path, value)
        self._settings = value
