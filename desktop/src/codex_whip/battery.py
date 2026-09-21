from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatteryStatus:
    percent: int
    charging: bool


def parse_battery_fields(fields: tuple[str, ...]) -> BatteryStatus:
    if len(fields) != 2:
        raise ValueError("BATTERY requires percent and charging state")
    try:
        percent = int(fields[0])
        charging_number = int(fields[1])
    except ValueError as exc:
        raise ValueError("BATTERY contains a non-numeric field") from exc
    if not 0 <= percent <= 100:
        raise ValueError("BATTERY percent is outside 0–100")
    if charging_number not in (0, 1):
        raise ValueError("BATTERY charging state must be 0 or 1")
    return BatteryStatus(percent, bool(charging_number))
