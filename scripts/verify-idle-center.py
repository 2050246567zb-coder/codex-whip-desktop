"""Observe live idle recentering without a GUI, sends, recording or profile writes."""
import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from bleak import BleakClient, BleakScanner
from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.models import DeviceMessage, RawMotionBatch
from codex_whip.mount_profile import default_mounting_path, load_mounting_profile
from codex_whip.protocol import LineDecoder
from codex_whip.sensor_pose import SensorPoseTracker


async def check():
    device = await BleakScanner.find_device_by_name('CodexWhip', timeout=8)
    if device is None:
        raise RuntimeError('未发现手柄广播。请关闭旧监听程序，并保持手柄供电。')
    queue = asyncio.Queue()
    decoder = LineDecoder()
    tracker = SensorPoseTracker(load_mounting_profile(default_mounting_path()))
    samples, last, max_gap = 0, None, 0
    events, status, errors = [], [], []
    def notify(_, data):
        try:
            for item in decoder.feed(data):
                queue.put_nowait(item)
        except Exception as exc:
            errors.append(str(exc))
    async with BleakClient(device, timeout=12) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notify)
        try:
            await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b'PING\n', response=False)
            await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b'RAW,2\n', response=False)
            print('LIVE_READY: 观察八秒原始数据，不发送消息。', flush=True)
            end = asyncio.get_running_loop().time() + 8
            while asyncio.get_running_loop().time() < end:
                try:
                    item = await asyncio.wait_for(queue.get(), .5)
                except asyncio.TimeoutError:
                    continue
                if isinstance(item, RawMotionBatch):
                    for frame in item.frames:
                        if last is not None:
                            max_gap = max(max_gap, (frame.timestamp_ms-last) & 0xffffffff)
                        last = frame.timestamp_ms
                        samples += 1
                    pose = tracker.feed_batch(item, auto_center=True)
                    if pose.auto_centered:
                        events.append({'timestamp_ms': last, 'pose': asdict(pose)})
                elif isinstance(item, DeviceMessage):
                    status.append(item.raw)
                # Deliberately ignore WHIP/audio; there is no sender/GUI/voice module.
        finally:
            if client.is_connected:
                await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b'RAW,0\n', response=False)
                await client.stop_notify(NUS_TX_CHARACTERISTIC)
    return {'samples': samples, 'max_sample_gap_ms': max_gap, 'status': status,
            'protocol_errors': errors, 'auto_center_events': events,
            'observed_live_idle_center': bool(events), 'profiles_written': False,
            'messages_sent': 0, 'physical_screen_animation_verified': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(check())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
