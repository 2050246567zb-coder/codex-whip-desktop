"""Hidden BLE device preference shared by Windows and macOS product builds."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .paths import user_data_dir


RSSI_HYSTERESIS_DB = 6


def _identity(device: object) -> str:
    return str(getattr(device, "address", "") or "").strip()


def _rssi(advertisement: object) -> int | None:
    value = getattr(advertisement, "rssi", None)
    return int(value) if isinstance(value, int | float) else None


def choose_device(
    candidates: Iterable[tuple[object, object]],
    remembered_identity: str = "",
) -> object | None:
    """Choose strongest signal, retaining the last device only for close ties.

    A six dB hysteresis prevents two nearby handles from alternating between
    reconnect attempts. A clearly nearer device still wins.
    """
    choices = list(candidates)
    if not choices:
        return None
    strongest = max(choices, key=lambda item: _rssi(item[1]) or -10_000)
    remembered = remembered_identity.strip().casefold()
    if not remembered:
        return strongest[0]
    previous = next(
        (item for item in choices if _identity(item[0]).casefold() == remembered),
        None,
    )
    if previous is None:
        return strongest[0]
    previous_rssi = _rssi(previous[1])
    strongest_rssi = _rssi(strongest[1])
    if previous_rssi is None or strongest_rssi is None:
        return previous[0]
    return previous[0] if previous_rssi >= strongest_rssi - RSSI_HYSTERESIS_DB else strongest[0]


class BleDevicePreferenceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_dir() / "ble-device-preference.json"

    def load(self) -> str:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = data.get("last_connected", "")
            return value.strip() if isinstance(value, str) else ""
        except (OSError, ValueError, TypeError, AttributeError):
            return ""

    def remember(self, identity: str) -> None:
        identity = identity.strip()
        if not identity:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"schema_version": 1, "last_connected": identity}, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)
