"""Opt-in device power preference; no detector/calibration values are changed."""
from __future__ import annotations
import json
from pathlib import Path


def supports_power_saving(version: str) -> bool:
    # XIAO nRF52840 supports this in 0.6.1+ and current 0.8.x.
    # The ESP32 0.7.x branch has not implemented this capability.
    try:
        parts = tuple(int(v) for v in version.split('.'))
        return len(parts) == 3 and (
            (0, 6, 1) <= parts < (0, 7, 0)
            or (0, 8, 1) <= parts < (0, 9, 0)
        )
    except ValueError:
        return False


class PowerSettings:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.enabled = json.loads(path.read_text(encoding='utf-8')).get('enabled') is True
        except (OSError, ValueError, AttributeError):
            self.enabled = False

    def save(self, enabled: bool):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'enabled': bool(enabled)}), encoding='utf-8')
        temporary.replace(self.path)
        self.enabled = bool(enabled)

    def command(self) -> str:
        return f'POWER,{int(self.enabled)}'
