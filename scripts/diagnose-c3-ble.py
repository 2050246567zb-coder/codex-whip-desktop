"""Read-only BLE protocol diagnosis. No sender or user-profile writes."""
import asyncio
import argparse
import json
import math
import time
import statistics
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from bleak import BleakClient, BleakScanner
from codex_whip.protocol import LineDecoder
from codex_whip.models import RawMotionBatch
from codex_whip.gui import CodexWhipWindow
from codex_whip.sensor_pose import SensorPoseTracker
from codex_whip.mount_profile import default_mounting_path, load_mounting_profile
from codex_whip.sensor_bias import load_sensor_bias
from codex_whip.paths import user_data_dir

parser = argparse.ArgumentParser()
parser.add_argument('--seconds', type=float, default=5)
parser.add_argument('--raw-mode', choices=('1', '2'), default='2')
parser.add_argument('--output', type=Path)
parser.add_argument('--device-name', help='Select exactly one advertising device by name instead of the C3 address')
args = parser.parse_args()

ADDRESS = 'E8:3D:C1:8D:D9:46'  # BLE advertisement address, not esptool base MAC
RX = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'
TX = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'

async def main():
    devices = await BleakScanner.discover(timeout=8, return_adv=True)
    for d,a in devices.values():
        if 'whip' in (d.name or '').lower():
            print('ADVERTISEMENT',d.address,d.name,a.service_uuids,flush=True)
    matches = [d for d,a in devices.values() if
               ((d.name == args.device_name or a.local_name == args.device_name)
                if args.device_name else d.address.upper() == ADDRESS)]
    if len(matches) > 1:
        raise RuntimeError('Multiple matching boards; power off the unused board first')
    device = matches[0] if matches else None
    if not device:
        raise RuntimeError('Board not advertising: '+(args.device_name or ADDRESS))
    print('DEVICE', device.address, device.name, flush=True)
    decoder=LineDecoder()
    counts=Counter()
    frames=[]
    arrivals=[]
    versions=[]
    tracker = SensorPoseTracker(load_mounting_profile(default_mounting_path()))
    bias = load_sensor_bias(user_data_dir() / 'sensor-bias-profiles.json', device.address)
    tracker.set_device_bias(bias)
    print('DEVICE_BIAS', bias, flush=True)
    fallback = SensorPoseTracker()
    poses=[]
    fallback_poses=[]
    def receive(_, data):
        try:
            for msg in decoder.feed(data):
                counts[type(msg).__name__]+=1
                if isinstance(msg, RawMotionBatch):
                    arrivals.append(time.perf_counter())
                    frames.extend(msg.frames)
                    poses.append(tracker.feed_batch(msg, auto_center=True))
                    fallback_poses.append(fallback.feed_batch(msg, auto_center=True))
                else:
                    print('RX', msg, flush=True)
                    if getattr(msg,'kind',None)=='PONG': versions.append(msg.fields[0])
        except Exception as exc:
            counts['decode_errors']+=1
            print('DECODE_ERROR', repr(exc), flush=True)
    async with BleakClient(device, timeout=15) as client:
        print('CONNECTED', client.is_connected, 'MTU', client.mtu_size, flush=True)
        for service in client.services:
            for ch in service.characteristics:
                if ch.uuid in (RX,TX): print('CHAR', ch.uuid, ch.properties, flush=True)
        await client.start_notify(TX, receive)
        await asyncio.sleep(.5)
        commands = ('PING','STATUS','CFG,GET','RAW,' + args.raw_mode) if args.device_name else (
            'PING','STATUS','IMU,GET','CFG,GET','BLE,GET','RAW,' + args.raw_mode)
        for cmd in commands:
            if cmd.startswith('RAW,'):
                assert versions and CodexWhipWindow._version_at_least(versions[-1],(0,6,0)), 'Desktop rejects firmware version'
            print('TX',cmd,flush=True)
            await client.write_gatt_char(RX,(cmd+'\n').encode(),response=False)
            await asyncio.sleep(1)
        print('MOTION_CAPTURE_READY', args.seconds, flush=True)
        await asyncio.sleep(args.seconds)
        await client.write_gatt_char(RX,b'RAW,0\n',response=False)
        await asyncio.sleep(.5)
        await client.stop_notify(TX)
    print('COUNTS',dict(counts),'FRAMES',len(frames),flush=True)
    if frames:
        print('FIRST',frames[0], 'LAST',frames[-1],flush=True)
        print('PEAK_GYRO_DPS', max(math.sqrt(f.gyro_x_dps**2+f.gyro_y_dps**2+f.gyro_z_dps**2) for f in frames), flush=True)
        for label, samples in [('SAVED_MOUNT', poses), ('DEFAULT_MOUNT', fallback_poses)]:
            print(label, {key: [round(min(getattr(p,key) for p in samples),2), round(max(getattr(p,key) for p in samples),2)] for key in ('offset_x','offset_y','angle_degrees')}, flush=True)
            print(label, 'AUTO_CENTER_EVENTS', sum(p.auto_centered for p in samples), flush=True)
        print('MEAN_GYRO_DPS', [round(statistics.mean(getattr(f,key) for f in frames),3)
                                for key in ('gyro_x_dps','gyro_y_dps','gyro_z_dps')], flush=True)
        print('ACCEL_NORM_G', [round(fn(math.sqrt(f.accel_x_g**2+f.accel_y_g**2+f.accel_z_g**2)
                                      for f in frames),3) for fn in (min,statistics.mean,max)], flush=True)
    for label, gaps in (
        ('BLE_BATCH_GAP_MS', [(b-a)*1000 for a,b in zip(arrivals,arrivals[1:])]),
        ('DEVICE_SAMPLE_GAP_MS', [(b.timestamp_ms-a.timestamp_ms)&0xffffffff for a,b in zip(frames,frames[1:])]),
    ):
        if gaps:
            ordered=sorted(gaps)
            print(label, {'median':round(statistics.median(gaps),2), 'p95':round(ordered[min(len(ordered)-1,math.ceil(len(ordered)*.95)-1)],2), 'max':round(max(gaps),2), 'over_100ms':sum(g>100 for g in gaps)}, flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'counts':dict(counts), 'batch_arrival_seconds':arrivals, 'frames':[asdict(f) for f in frames], 'poses':[asdict(p) for p in poses], 'fallback_poses':[asdict(p) for p in fallback_poses]}, indent=2), encoding='utf-8')
    if len(frames)<400 or counts['decode_errors']:
        raise RuntimeError('RAW stream missing, stalled or invalid (expected >=400 frames)')

asyncio.run(main())
