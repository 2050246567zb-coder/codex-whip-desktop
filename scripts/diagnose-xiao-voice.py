from __future__ import annotations

import argparse
import asyncio
import time

from bleak import BleakClient, BleakScanner

from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.ble_preference import BleDevicePreferenceStore, choose_device
from codex_whip.models import AudioChunk, AudioEnd, AudioStart, DeviceMessage
from codex_whip.protocol import LineDecoder


async def main(seconds: float, settle: float) -> int:
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

    decoder = LineDecoder()
    received_chunks = 0
    received_samples = 0
    ended = asyncio.Event()
    ack_queue: asyncio.Queue[tuple[int, int]] = asyncio.Queue()

    def notification(_sender, data: bytearray) -> None:
        nonlocal received_chunks, received_samples
        for message in decoder.feed(data):
            if isinstance(message, AudioStart):
                print(
                    f"VOICE_START,{message.session},{message.sample_rate},{message.codec}",
                    flush=True,
                )
            elif isinstance(message, AudioChunk):
                received_chunks += 1
                received_samples += message.sample_count
                if message.flow_controlled and (message.sequence == 0 or message.sequence % 4 == 0):
                    ack_queue.put_nowait((message.session, message.sequence))
                if received_chunks <= 3 or received_chunks % 20 == 0:
                    print(
                        f"AUDIO,{message.session},{message.sequence},{message.sample_count}",
                        flush=True,
                    )
            elif isinstance(message, AudioEnd):
                print(
                    f"VOICE_END,{message.session},{message.total_samples},{message.reason}",
                    flush=True,
                )
                ended.set()
            elif isinstance(message, DeviceMessage):
                if message.kind in {"VOICE", "ERR", "WARN", "LINK", "PONG"}:
                    print(message.raw, flush=True)

    async with BleakClient(device, timeout=15.0) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notification)
        for command in ("PING", "HOST,WINDOWS", "VOICE,1"):
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC,
                (command + "\n").encode("ascii"),
                response=False,
            )
            await asyncio.sleep(0.08)
        await asyncio.sleep(settle)
        for command in ("LINK", "VOICE,START,1200,15000"):
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC,
                (command + "\n").encode("ascii"),
                response=False,
            )
            await asyncio.sleep(0.08)
        print("SPEAK_NOW", flush=True)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not ended.is_set():
            try:
                session, sequence = ack_queue.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.01)
            else:
                await client.write_gatt_char(
                    NUS_RX_CHARACTERISTIC,
                    f"VOICE,ACK,{session},{sequence}\n".encode("ascii"),
                    response=False,
                )
        if not ended.is_set():
            await client.write_gatt_char(
                NUS_RX_CHARACTERISTIC, b"VOICE,CANCEL\n", response=False
            )
            await asyncio.sleep(0.3)
        await client.stop_notify(NUS_TX_CHARACTERISTIC)
    print(f"SUMMARY,CHUNKS,{received_chunks},SAMPLES,{received_samples}", flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--settle", type=float, default=2.0)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.seconds, args.settle)))
