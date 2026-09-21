"""Read-only motion test plus reversible POWER/RAW settings; never sends app messages.

Run with the desktop app closed. Leaves the original POWER preference restored.
The user must leave the board still for 5 minutes, then move when prompted.
"""
import asyncio
import json
import time
import math
from pathlib import Path

from bleak import BleakClient, BleakScanner
from codex_whip.protocol import LineDecoder
from codex_whip.models import DeviceMessage, RawMotionBatch
from codex_whip.ble_client import NUS_RX_CHARACTERISTIC as RX, NUS_TX_CHARACTERISTIC as TX


async def main():
    result = {'events': [], 'frames': 0, 'motion_snapshots': []}
    started = time.monotonic()
    queue = asyncio.Queue()
    decoder = LineDecoder()

    def receive(_, data):
        for message in decoder.feed(data):
            if isinstance(message, RawMotionBatch):
                result['frames'] += len(message.frames)
                if result['frames'] % 100 == 0:
                    f=message.frames[-1]
                    result['motion_snapshots'].append({'seconds':round(time.monotonic()-started,2),
                        'gyro': [f.gyro_x_dps,f.gyro_y_dps,f.gyro_z_dps],
                        'accel':[f.accel_x_g,f.accel_y_g,f.accel_z_g]})
            elif isinstance(message, DeviceMessage):
                result['events'].append({'seconds': round(time.monotonic()-started,3),
                                         'kind': message.kind,'fields': message.fields})
                print(message.kind, ','.join(message.fields), flush=True)
                queue.put_nowait(message)

    async def wait(kind, predicate=lambda _:True, timeout=10):
        async def find():
            while True:
                item = await queue.get()
                if item.kind == kind and predicate(item): return item
        return await asyncio.wait_for(find(), timeout)

    device = await BleakScanner.find_device_by_address('E2:30:F0:9F:D1:21', timeout=15)
    if device is None: raise RuntimeError('Old XIAO not found')
    original = None
    try:
        async with BleakClient(device) as client:
            async def send(value):
                await client.write_gatt_char(RX,(value+'\n').encode('ascii'),response=False)
                await asyncio.sleep(.06)
            await client.start_notify(TX,receive)
            await send('PING')
            pong=await wait('PONG')
            assert pong.fields[0]=='0.7.1',pong
            await send('POWERTEST')
            checks=await wait('POWERTEST')
            assert checks.fields==('12','12'),checks
            await send('POWERGET')
            original=(await wait('POWER')).fields[0]
            try:
                await send('POWER,0')
                await wait('POWER',lambda m:m.fields[0]=='0')
                await send('ARM,1')
                await send('RAW,2')
                await asyncio.sleep(2)
                assert result['frames']>0,'No active stream'
                await send('POWER,1')
                await wait('POWER',lambda m:m.fields[:2]==('1','ACTIVE'))
                idle_start=time.monotonic()
                print('WAITING_STILL_5_MINUTES',flush=True)
                await wait('POWER',lambda m:m.fields[1]=='SLEEP',timeout=390)
                result['sleep_after_seconds']=round(time.monotonic()-idle_start,3)
                assert result['sleep_after_seconds']>=299
                await asyncio.sleep(1)
                count=result['frames']
                await asyncio.sleep(2)
                assert result['frames']==count,'Raw stream continued during sleep'
                await send('PING')
                await wait('PONG')
                result['sleep_ble_alive']=True
                print('PLEASE_MOVE_NOW',flush=True)
                await wait('POWER',lambda m:m.fields[1]=='ACTIVE',timeout=120)
                await asyncio.sleep(3)
                assert result['frames']>count,'Raw stream did not resume'
                result['motion_wake_and_stream']=True
                print('WAKE_VERIFIED',flush=True)
            finally:
                if client.is_connected:
                    await send('POWER,'+original)
                    await wait('POWER',lambda m:m.fields[0]==original)
                    await send('RAW,0')
                    await wait('RAW')
            result['passed']=True
    finally:
        Path('output/xiao-power-test.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


if __name__=='__main__':
    asyncio.run(main())
