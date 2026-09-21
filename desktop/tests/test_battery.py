import pytest

from codex_whip.battery import BatteryStatus, parse_battery_fields


def test_battery_protocol_fields_are_strict():
    assert parse_battery_fields(("68", "0")) == BatteryStatus(68, False)
    assert parse_battery_fields(("99", "1")) == BatteryStatus(99, True)
    for fields in (("101", "0"), ("20", "2"), ("bad", "0"), ("50",)):
        with pytest.raises(ValueError):
            parse_battery_fields(fields)
