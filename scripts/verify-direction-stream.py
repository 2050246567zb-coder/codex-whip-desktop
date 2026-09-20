"""Check live RAW data for the new wizard without learning/saving any user pose."""
import argparse
import asyncio
import json
import threading
from pathlib import Path

from bleak import BleakClient, BleakScanner
from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.gui import GuiEventProcessor
from codex_whip.models import DeviceMessage, RawMotionBatch
from codex_whip.protocol import LineDecoder
from codex_whip.settings import Settings


async def check():
    device = await BleakScanner.find_device_by_name("CodexWhip", timeout=8)
    if device is None:
        raise RuntimeError("未发现 CodexWhip 广播，请开启手柄并关闭旧监听程序。")
    queue = asyncio.Queue()
    decoder = LineDecoder()
    events, frames, status, errors = [], [], [], []
    processor = GuiEventProcessor(Settings(), threading.Event(), lambda *a: events.append(a))
    processor.mount_command("open", "diagnostic")

    def notify(_, data):
        try:
            for item in decoder.feed(data):
                queue.put_nowait(item)
        except Exception as exc:
            errors.append(str(exc))

    async with BleakClient(device, timeout=12) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notify)
        try:
            await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b"PING\n", response=False)
            await asyncio.sleep(.1)
            await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b"RAW,2\n", response=False)
            end = asyncio.get_running_loop().time() + 6
            while asyncio.get_running_loop().time() < end:
                try:
                    item = await asyncio.wait_for(queue.get(), .5)
                except asyncio.TimeoutError:
                    continue
                await processor.handle(item)
                if isinstance(item, RawMotionBatch):
                    frames.extend(item.frames)
                elif isinstance(item, DeviceMessage):
                    status.append(item.raw)
        finally:
            if client.is_connected:
                await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b"RAW,0\n", response=False)
                await client.stop_notify(NUS_TX_CHARACTERISTIC)
    gaps = [(b.timestamp_ms-a.timestamp_ms) & 0xFFFFFFFF for a, b in zip(frames, frames[1:])]
    report = {"status": status, "samples": len(frames),
              "max_sample_gap_ms": max(gaps, default=0), "protocol_errors": errors,
              "large_gaps": [{"after_index": i, "from_ms": frames[i].timestamp_ms,
                              "to_ms": frames[i+1].timestamp_ms, "gap_ms": gap}
                             for i, gap in enumerate(gaps) if gap > 120],
              "stationary_window_available": processor._sensor_pose.stationary_reading() is not None,
              "wizard_stage": processor._mount_session.stage,
              "calibration_stream_errors": [p["detail"] for k, p in events if k == "mount_state" and p.get("error")],
              "whip_or_send_events": sum(k in ("whip", "send_result", "send_error") for k, _ in events),
              "profiles_written": False, "physical_direction_learning_verified": False}
    report["passed"] = (len(frames) >= 400 and max(gaps, default=999) <= 120
                        and not errors and report["whip_or_send_events"] == 0
                        and not report["calibration_stream_errors"])
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(check())
    data = json.dumps(report, ensure_ascii=False, indent=2)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(data + "\n", encoding="utf-8")
    print(data)
    raise SystemExit(0 if report["passed"] else 1)
