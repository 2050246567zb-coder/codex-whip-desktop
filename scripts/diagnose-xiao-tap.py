from __future__ import annotations

import argparse
import asyncio
import time

from bleak import BleakClient, BleakScanner

from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.ble_preference import BleDevicePreferenceStore, choose_device


async def main(seconds: float, threshold: float) -> int:
    found = await BleakScanner.discover(timeout=8.0, return_adv=True)
    candidates = [
        (device, advertisement)
        for device, advertisement in found.values()
        if (advertisement.local_name or device.name) == "CodexWhip"
    ]
    if not candidates:
        print("NO_DEVICE", flush=True)
        return 2
    for candidate, advertisement in candidates:
        print(
            f"CANDIDATE,{candidate.address},{advertisement.rssi},"
            f"{'|'.join(advertisement.service_uuids or [])}",
            flush=True,
        )
    remembered = BleDevicePreferenceStore().load()
    device = choose_device(candidates, remembered)
    if device is None:
        return 2
    print(f"SELECTED,{device.address},REMEMBERED,{remembered}", flush=True)
    buffer = bytearray()

    def notification(_sender, data: bytearray) -> None:
        buffer.extend(data)
        while b"\n" in buffer:
            raw, _, tail = buffer.partition(b"\n")
            buffer[:] = tail
            text = raw.rstrip(b"\r").decode("ascii", "replace")
            print(text, flush=True)

    async with BleakClient(device, timeout=15.0) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notification)
        for command in (
            "PING",
            "ARM,1",
            f"TAPCFG,{threshold:.2f}",
            "VOICE,1",
            "STATUS",
        ):
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC,
                (command + "\n").encode("ascii"),
                response=False,
            )
            await asyncio.sleep(0.08)
        print(f"TAP_CAPTURE_READY,{seconds:.0f}", flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await client.stop_notify(NUS_TX_CHARACTERISTIC)
    print("TAP_CAPTURE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument("--threshold", type=float, default=1.25)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.seconds, args.threshold)))
