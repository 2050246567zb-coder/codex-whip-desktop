import asyncio
import struct
from types import SimpleNamespace
import pytest

from codex_whip.ble_preference import BleDevicePreferenceStore, choose_device
from codex_whip.ble_client import drain_message_queue
from codex_whip.models import DeviceMessage
from codex_whip.protocol import crc16_ccitt


@pytest.mark.parametrize('platform,expected', [
    ('darwin', 'HOST,MACOS'), ('win32', 'HOST,WINDOWS'),
    ('linux', 'HOST,LINUX'), ('unknown', 'HOST,COMPATIBLE'),
])
def test_host_profile_mapping(platform, expected):
    from codex_whip.ble_client import host_profile_command
    assert host_profile_command(platform) == expected


def _candidate(identity: str, rssi: int):
    return SimpleNamespace(address=identity, name='CodexWhip'), SimpleNamespace(
        local_name='CodexWhip', rssi=rssi)


def test_nearest_device_wins_and_recent_device_breaks_close_tie():
    near = _candidate('NEAR', -41)
    recent = _candidate('RECENT', -45)
    assert choose_device((near, recent), '') is near[0]
    assert choose_device((near, recent), 'recent') is recent[0]
    far_recent = _candidate('RECENT', -61)
    assert choose_device((near, far_recent), 'RECENT') is near[0]


def test_device_preference_is_hidden_and_persistent(tmp_path):
    store = BleDevicePreferenceStore(tmp_path / 'ble-device-preference.json')
    assert store.load() == ''
    store.remember('AA:BB:CC')
    assert store.load() == 'AA:BB:CC'
    assert 'AA:BB:CC' in store.path.read_text(encoding='utf-8')


def test_scan_filters_name_then_selects_signal_and_memory(monkeypatch, tmp_path):
    from codex_whip import ble_client
    from codex_whip.settings import BleSettings

    async def exercise():
        recent = _candidate('RECENT', -47)
        strongest = _candidate('STRONG', -43)
        unrelated = (SimpleNamespace(address='OTHER', name='Other'),
                     SimpleNamespace(local_name='Other', rssi=-20))
        async def discover(**_kwargs):
            return {'recent': recent, 'strong': strongest, 'other': unrelated}
        monkeypatch.setattr(ble_client.BleakScanner, 'discover', discover)
        store = BleDevicePreferenceStore(tmp_path / 'preference.json')
        store.remember('RECENT')
        client = ble_client.BleWhipClient(BleSettings(), device_preference=store)
        assert await client._scan_preferred_device() is recent[0]
    asyncio.run(exercise())


@pytest.mark.parametrize('caps,expected', [
    (b'CAPS,HOST_PROFILE,1\n', 1), (b'', 0),
    (b'CAPS,HOST_PROFILE,2\n', 0),
])
def test_host_negotiates_once_per_connection_only_when_supported(monkeypatch, caps, expected):
    from codex_whip import ble_client
    from codex_whip.settings import BleSettings
    monkeypatch.setattr(ble_client.sys, 'platform', 'darwin')

    async def exercise():
        writes = []
        stop = asyncio.Event()
        class Peripheral:
            is_connected = True
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def start_notify(self, uuid, callback):
                self.notify = callback
                callback(None, b'PONG,0.7.4\n' + caps * 3)
            async def stop_notify(self, uuid): pass
            async def write_gatt_char(self, uuid, payload, **kwargs):
                writes.append(payload)
        peripheral = Peripheral()
        monkeypatch.setattr(ble_client, 'BleakClient', lambda *a, **k: peripheral)
        async def handler(item):
            stop.set()
        client = ble_client.BleWhipClient(BleSettings())
        for _ in range(2):
            stop.clear()
            await client._run_connection(object(), handler, stop)
        assert writes.count(b'HOST,MACOS\n') == expected * 2
        assert writes.count(b'PING\n') == 2
        assert writes.count(b'ARM,1\n') == 2
    asyncio.run(exercise())


def message(value: str) -> DeviceMessage:
    return DeviceMessage("TEST", (value,), f"TEST,{value}")


def test_drain_message_queue_processes_existing_audio_burst_in_order() -> None:
    queue: asyncio.Queue[DeviceMessage] = asyncio.Queue()
    for value in ("1", "2", "3"):
        queue.put_nowait(message(value))
    received: list[str] = []

    async def handler(item: DeviceMessage) -> None:
        received.append(item.fields[0])

    drained = asyncio.run(drain_message_queue(queue, handler))

    assert drained == 3
    assert received == ["1", "2", "3"]
    assert queue.empty()


def test_drain_message_queue_honors_burst_limit() -> None:
    queue: asyncio.Queue[DeviceMessage] = asyncio.Queue()
    for value in ("1", "2", "3"):
        queue.put_nowait(message(value))

    async def handler(_item: DeviceMessage) -> None:
        return None

    drained = asyncio.run(drain_message_queue(queue, handler, limit=2))

    assert drained == 2
    assert queue.qsize() == 1


def test_continuous_notifications_do_not_starve_commands(monkeypatch) -> None:
    from codex_whip import ble_client
    from codex_whip.settings import BleSettings

    async def exercise():
        stop = asyncio.Event()
        commands = asyncio.Queue()
        writes = []
        received = 0

        class Peripheral:
            is_connected = True

            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def start_notify(self, uuid, callback):
                self.notify = callback
                self.notify(None, b'PONG,0.6.0\n' * 64)

            async def stop_notify(self, uuid):
                pass

            async def write_gatt_char(self, uuid, payload, **kwargs):
                writes.append(payload)
                if payload == b'RAW,1\n':
                    stop.set()

        peripheral = Peripheral()
        monkeypatch.setattr(ble_client, 'BleakClient', lambda *a, **k: peripheral)

        async def handler(item):
            nonlocal received
            received += 1
            if received == 1:
                commands.put_nowait('CFG,CG,150')
                commands.put_nowait('RAW,1')
            peripheral.notify(None, b'PONG,0.6.0\n')
            if received >= 256:
                stop.set()  # bounded failure on the old starvation loop

        client = ble_client.BleWhipClient(BleSettings(), command_queue=commands)
        await client._run_connection(object(), handler, stop)
        assert b'CFG,CG,150\n' in writes
        assert b'RAW,1\n' in writes
        assert received < 256

    asyncio.run(exercise())


def test_idle_connection_polls_battery_without_waiting_for_notifications(monkeypatch) -> None:
    from codex_whip import ble_client
    from codex_whip.settings import BleSettings

    async def exercise():
        stop = asyncio.Event()
        writes = []

        class Peripheral:
            is_connected = True

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def start_notify(self, _uuid, _callback):
                pass

            async def stop_notify(self, _uuid):
                pass

            async def write_gatt_char(self, _uuid, payload, **_kwargs):
                writes.append(payload)
                if payload == b"BATTERY\n":
                    stop.set()

        monkeypatch.setattr(ble_client, "BATTERY_POLL_SECONDS", 0.01)
        monkeypatch.setattr(ble_client, "BleakClient", lambda *a, **k: Peripheral())
        client = ble_client.BleWhipClient(BleSettings())

        async def handler(_item):
            pass

        await asyncio.wait_for(
            client._run_connection(object(), handler, stop), timeout=0.5
        )
        assert writes[:2] == [b"PING\n", b"ARM,1\n"]
        assert b"BATTERY\n" in writes

    asyncio.run(exercise())


def test_binary_audio_is_acknowledged_without_waiting_for_cloud_processing(monkeypatch) -> None:
    from codex_whip import ble_client
    from codex_whip.settings import BleSettings

    async def exercise():
        stop = asyncio.Event()
        writes = []
        payload = b'\x12\x34'
        body = struct.pack('<BBHIIHhB', 1, 1, 13 + len(payload), 9, 0, 5, 0, 0) + payload
        frame = b'\xA5\x5A' + body + struct.pack('<H', crc16_ccitt(body))

        class Peripheral:
            is_connected = True
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass
            async def start_notify(self, _uuid, callback):
                callback(None, b'VOICE,START,9,16000,IMA_ADPCM4\n' + frame)
            async def stop_notify(self, _uuid): pass
            async def write_gatt_char(self, _uuid, data, **_kwargs):
                writes.append(data)
                if data == b'VOICE,ACK,9,0\n':
                    stop.set()

        monkeypatch.setattr(ble_client, 'BleakClient', lambda *a, **k: Peripheral())
        client = ble_client.BleWhipClient(BleSettings())
        async def handler(_item): pass
        await asyncio.wait_for(client._run_connection(object(), handler, stop), timeout=.5)
        assert b'VOICE,ACK,9,0\n' in writes
        assert b'BATTERY\n' not in writes

    asyncio.run(exercise())
