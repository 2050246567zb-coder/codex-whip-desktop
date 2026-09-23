from __future__ import annotations

import argparse
import asyncio
import heapq
import math
import time

from bleak import BleakClient, BleakScanner

from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.ble_preference import BleDevicePreferenceStore, choose_device
from codex_whip.models import DeviceMessage, RawMotionBatch
from codex_whip.protocol import LineDecoder


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
    decoder = LineDecoder()
    peaks: list[tuple[float, int, float, float]] = []
    frames = 0
    previous = None
    tap_messages: list[str] = []

    def notification(_sender, data: bytearray) -> None:
        nonlocal frames, previous
        for message in decoder.feed(data):
            if isinstance(message, DeviceMessage):
                if message.kind.startswith("TAP"):
                    tap_messages.append(message.raw)
                    print(message.raw, flush=True)
                continue
            if not isinstance(message, RawMotionBatch):
                continue
            for frame in message.frames:
                frames += 1
                accel = (frame.accel_x_g, frame.accel_y_g, frame.accel_z_g)
                gyro = math.sqrt(
                    frame.gyro_x_dps**2
                    + frame.gyro_y_dps**2
                    + frame.gyro_z_dps**2
                )
                if previous is not None:
                    slope = math.dist(accel, previous)
                    item = (slope, frame.timestamp_ms, math.sqrt(sum(v * v for v in accel)), gyro)
                    if len(peaks) < 16:
                        heapq.heappush(peaks, item)
                    elif item > peaks[0]:
                        heapq.heapreplace(peaks, item)
                previous = accel

    async with BleakClient(device, timeout=15.0) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notification)
        for command in (
            "PING",
            "ARM,1",
            f"TAPCFG,{threshold:.2f}",
            "VOICE,1",
            "RAW,2",
        ):
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC,
                (command + "\n").encode("ascii"),
                response=False,
            )
            await asyncio.sleep(0.08)
        print(f"RAW_TAP_CAPTURE_READY,{seconds:.0f}", flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await client.write_gatt_char(
            NUS_RX_CHARACTERISTIC, b"RAW,0\n", response=False
        )
        await asyncio.sleep(0.2)
        await client.stop_notify(NUS_TX_CHARACTERISTIC)

    print(f"FRAMES,{frames}")
    print(f"TAP_MESSAGES,{len(tap_messages)}")
    for slope, timestamp, magnitude, gyro in sorted(peaks, reverse=True):
        print(f"PEAK,{timestamp},{slope:.3f},{magnitude:.3f},{gyro:.1f}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--threshold", type=float, default=1.25)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.seconds, args.threshold)))
