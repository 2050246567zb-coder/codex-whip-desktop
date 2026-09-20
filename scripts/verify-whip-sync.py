"""Verify threshold readback and capture motion without a sender or profile writes."""
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from bleak import BleakScanner
from codex_whip.ble_client import BleWhipClient
from codex_whip.calibration import load_profile
from codex_whip.models import DeviceMessage, RawMotionBatch, WhipEvent
from codex_whip.settings import BleSettings


async def main():
    commands = asyncio.Queue()
    stop = asyncio.Event()
    ready = asyncio.Event()
    profile = load_profile()
    records = []
    synced = False

    async def handle(item):
        nonlocal synced
        records.append({'type': type(item).__name__, 'data': asdict(item)})
        if isinstance(item, DeviceMessage):
            if item.kind == 'PONG' and not synced:
                synced = True
                for command in (*profile.commands(), 'RAW,1', 'CFG,GET'):
                    commands.put_nowait(command)
            if item.kind == 'CFGVAL' and item.fields == ('DONE',):
                ready.set()
            if item.kind in ('CFG', 'CFGVAL', 'ERR', 'REJECT2'):
                print(item.raw, flush=True)
        elif isinstance(item, WhipEvent):
            print('WHIP', asdict(item), flush=True)

    device = await BleakScanner.find_device_by_name('CodexWhip', timeout=10)
    if device is None:
        raise RuntimeError('Device not found')
    client = BleWhipClient(BleSettings(), command_queue=commands)
    task = asyncio.create_task(client._run_connection(device, handle, stop))
    try:
        await asyncio.wait_for(ready.wait(), 15)
        values = {r['data']['fields'][0]: float(r['data']['fields'][1])
                  for r in records if r['type'] == 'DeviceMessage'
                  and r['data']['kind'] == 'CFGVAL' and len(r['data']['fields']) == 2}
        expected = {c.split(',')[1]: float(c.split(',')[2]) for c in profile.commands()}
        assert values == expected, (values, expected)
        print('SYNC_VERIFIED; ACTION_CAPTURE_READY (45 seconds)', flush=True)
        await asyncio.sleep(45)
    finally:
        stop.set()
        await task
        path = Path('outputs/whip-sync-live.json')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, ensure_ascii=False), encoding='utf-8')


asyncio.run(main())
