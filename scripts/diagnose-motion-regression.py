"""Read sensor frames, actual thresholds and WHIP events; no sender or profile writes."""
import argparse
import asyncio
import json
import math
import statistics
from dataclasses import asdict
from pathlib import Path

from bleak import BleakClient, BleakScanner
from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.models import DeviceMessage, RawMotionBatch, WhipEvent
from codex_whip.protocol import LineDecoder


async def capture(seconds):
    device = await BleakScanner.find_device_by_name('CodexWhip', timeout=8)
    if device is None:
        raise RuntimeError('手柄未广播，请保持供电并关闭旧版。')
    decoder = LineDecoder()
    frames, events, status, errors = [], [], [], []
    def notify(_, data):
        try:
            for item in decoder.feed(data):
                if isinstance(item, RawMotionBatch):
                    frames.extend(item.frames)
                elif isinstance(item, WhipEvent):
                    events.append({'last_sensor_ms': frames[-1].timestamp_ms if frames else None,
                                   'event': asdict(item)})
                elif isinstance(item, DeviceMessage):
                    status.append(item.raw)
        except Exception as exc:
            errors.append(str(exc))
    async with BleakClient(device, timeout=12) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, notify)
        try:
            for command in ('PING', 'STATUS', 'CFG,GET', 'RAW,1'):
                await client.write_gatt_char(NUS_RX_CHARACTERISTIC, (command+'\n').encode(), response=False)
                await asyncio.sleep(.15)
            print('LIVE_READY: 只做轻微移动，间隔停留。不会发送消息。', flush=True)
            await asyncio.sleep(seconds)
        finally:
            if client.is_connected:
                await client.write_gatt_char(NUS_RX_CHARACTERISTIC, b'RAW,0\n', response=False)
                await client.stop_notify(NUS_TX_CHARACTERISTIC)
    gyros = [math.sqrt(f.gyro_x_dps**2+f.gyro_y_dps**2+f.gyro_z_dps**2) for f in frames]
    gaps = [(b.timestamp_ms-a.timestamp_ms) & 0xffffffff for a,b in zip(frames, frames[1:])]
    return {'samples': len(frames), 'max_gap_ms': max(gaps, default=0),
            'gyro_peak_dps': max(gyros, default=0), 'gyro_median_dps': statistics.median(gyros) if gyros else 0,
            'status': status, 'events': events, 'protocol_errors': errors,
            'frames': [asdict(f) for f in frames], 'messages_sent': 0, 'profiles_written': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--seconds', type=float, default=30)
    args = parser.parse_args()
    report = asyncio.run(capture(min(45, max(3, args.seconds))))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'frames'}, ensure_ascii=False, indent=2))
