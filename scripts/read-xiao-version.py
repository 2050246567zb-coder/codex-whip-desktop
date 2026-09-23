from __future__ import annotations

import asyncio

from bleak import BleakClient, BleakScanner

from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.ble_preference import BleDevicePreferenceStore, choose_device


async def main() -> int:
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    candidates = [
        (device, advertisement)
        for device, advertisement in found.values()
        if (advertisement.local_name or device.name) == "CodexWhip"
    ]
    remembered = BleDevicePreferenceStore().load()
    device = choose_device(candidates, remembered)
    if device is None:
        print("NO_DEVICE", flush=True)
        return 2
    print(f"SELECTED,{device.address},REMEMBERED,{remembered}", flush=True)
    complete = asyncio.Event()
    buffer = bytearray()

    def notification(_sender: object, data: bytearray) -> None:
        buffer.extend(data)
        while b"\n" in buffer:
            raw, _, tail = buffer.partition(b"\n")
            buffer[:] = tail
            line = raw.rstrip(b"\r").decode("ascii", "replace")
            print(line, flush=True)
            if line.startswith("PONG,"):
                complete.set()

    async with BleakClient(device, timeout=15.0) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notification)
        await client.write_gatt_char(
            NUS_RX_CHARACTERISTIC, b"PING\n", response=False
        )
        try:
            await asyncio.wait_for(complete.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            print("NO_PONG", flush=True)
            return 3
        await client.stop_notify(NUS_TX_CHARACTERISTIC)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
