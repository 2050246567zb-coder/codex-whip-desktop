"""Read-only-to-PC device diagnostics: never import or call a Codex sender.

Applies the existing detector profile to volatile device RAM, runs status-only
self-tests, then reads RAW5. Does not start recording or edit user profiles.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from pathlib import Path

from bleak import BleakClient, BleakScanner
from codex_whip.ble_client import NUS_RX_CHARACTERISTIC, NUS_TX_CHARACTERISTIC
from codex_whip.calibration import load_profile
from codex_whip.models import DeviceMessage, RawMotionBatch
from codex_whip.protocol import LineDecoder
from codex_whip.sensor_pose import SensorPoseTracker


async def verify():
    discovered = await BleakScanner.discover(timeout=6.0, return_adv=True)
    matches = [device for device, ad in discovered.values()
               if (ad.local_name or device.name) == 'CodexWhip']
    if len(matches) != 1:
        raise RuntimeError(f'Expected one CodexWhip, found {len(matches)}; close the desktop listener.')
    decoder = LineDecoder()
    statuses = []
    batches = []
    errors = []
    arrivals = []

    def receive(_, payload):
        try:
            for message in decoder.feed(payload):
                if isinstance(message, DeviceMessage):
                    statuses.append(message.raw)
                elif isinstance(message, RawMotionBatch):
                    batches.append(message)
                    arrivals.append(time.monotonic())
        except Exception as exc:
            errors.append(str(exc))

    async with BleakClient(matches[0], timeout=15) as client:
        await client.start_notify(NUS_TX_CHARACTERISTIC, receive)

        async def command(value):
            await client.write_gatt_char(NUS_RX_CHARACTERISTIC, (value + '\n').encode(), response=False)
            await asyncio.sleep(0.06)

        try:
            await command('PING')
            await asyncio.sleep(0.5)
            if 'PONG,0.6.0' not in statuses:
                raise RuntimeError(f'Unexpected firmware status: {statuses}')
            await command('RAW,2')
            for value in load_profile().commands():
                await command(value)
            await command('RAW,0')
            await command('SELFTEST')
            for _ in range(40):
                if any(s.startswith('SELFTEST,DONE,') for s in statuses):
                    break
                await asyncio.sleep(0.1)
            # Keep the capture separate from command and self-test traffic.
            batches.clear()
            arrivals.clear()
            await command('RAW,2')
            await command('ARM,1')
            await asyncio.sleep(8.0)
        finally:
            if client.is_connected:
                await command('RAW,0')
                await client.stop_notify(NUS_TX_CHARACTERISTIC)

    frames = [f for batch in batches for f in batch.frames]
    if len(frames) < 100:
        raise RuntimeError(f'Only {len(frames)} sensor samples received; errors={errors}')
    tracker = SensorPoseTracker()
    for batch in batches:
        tracker.feed_batch(batch)
    gaps = [b.timestamp_ms - a.timestamp_ms for a, b in zip(frames, frames[1:])]
    sequence_gaps = [b.sequence - a.sequence for a, b in zip(batches, batches[1:])]
    recent = [f for f in frames if f.timestamp_ms >= frames[-1].timestamp_ms - 500]
    gyro_vectors = [(f.gyro_x_dps, f.gyro_y_dps, f.gyro_z_dps) for f in recent]
    accel_vectors = [(f.accel_x_g, f.accel_y_g, f.accel_z_g) for f in recent]
    gyro_mean = tuple(statistics.mean(v[i] for v in gyro_vectors) for i in range(3))
    accel_mean = tuple(statistics.mean(v[i] for v in accel_vectors) for i in range(3))
    report = {
        'firmware': '0.6.0',
        'selftests': [s for s in statuses if s.startswith('SELFTEST,')],
        'samples': len(frames), 'batches': len(batches),
        'sensor_hz': round((len(frames)-1)*1000/(frames[-1].timestamp_ms-frames[0].timestamp_ms), 2),
        'sample_delta_ms_median': statistics.median(gaps),
        'sample_delta_ms_max': max(gaps),
        'missing_batches': sum(max(0, gap-1) for gap in sequence_gaps),
        'protocol_errors': errors,
        'device_warnings': [s for s in statuses if s.startswith(('WARN,', 'ERR,'))],
        'gyro_examples_dps': sorted({f.gyro_x_dps for f in frames})[:20],
        'accel_examples_g': sorted({f.accel_y_g for f in frames})[:20],
        'stationary_calibration_available': tracker.calibrate_neutral() is not None,
        'calibration_window_gyro_mean': gyro_mean,
        'calibration_window_gyro_peak_variation': max(math.dist(v, gyro_mean) for v in gyro_vectors),
        'calibration_window_accel_mean': accel_mean,
        'calibration_window_accel_peak_variation': max(math.dist(v, accel_mean) for v in accel_vectors),
        'codex_messages_sent': 0,
        'physical_gesture_accuracy': 'not measured by synthetic tests',
    }
    report['passed'] = (not errors and 'SELFTEST,DONE,12,12' in statuses
                        and not report['device_warnings']
                        and report['missing_batches'] == 0
                        and report['sensor_hz'] >= 80 and max(gaps) < 100)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    result = asyncio.run(verify())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + '\n', encoding='utf-8')
    raise SystemExit(0 if result['passed'] else 1)
