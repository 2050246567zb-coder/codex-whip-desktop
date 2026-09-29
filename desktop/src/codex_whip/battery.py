from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BatteryStatus:
    percent: int
    charging: bool
    external_power: bool = False


def parse_battery_fields(fields: tuple[str, ...]) -> BatteryStatus:
    if len(fields) not in (2, 3):
        raise ValueError("BATTERY requires percent, charging and optional external power")
    try:
        percent = int(fields[0])
        charging_number = int(fields[1])
        power_number = int(fields[2]) if len(fields) == 3 else charging_number
    except ValueError as exc:
        raise ValueError("BATTERY contains a non-numeric field") from exc
    if not 0 <= percent <= 100:
        raise ValueError("BATTERY percent is outside 0–100")
    if charging_number not in (0, 1):
        raise ValueError("BATTERY charging state must be 0 or 1")
    if power_number not in (0, 1):
        raise ValueError("BATTERY external power state must be 0 or 1")
    return BatteryStatus(percent, bool(charging_number), bool(power_number or charging_number))
