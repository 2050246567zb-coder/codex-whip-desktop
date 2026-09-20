import asyncio
import json
import math
import threading
from types import SimpleNamespace

import pytest

from codex_whip.ble_client import BleWhipClient
from codex_whip.gui import GuiEventProcessor
from codex_whip.models import RawMotionBatch, RawMotionFrame
from codex_whip.sensor_bias import load_sensor_bias
from codex_whip.sensor_pose import SensorPoseTracker
from codex_whip.settings import BleSettings, Settings

BIAS = (1.829875, -3.611, -.4235)
IDENTITY = 'TEST-XIAO'


def batch(t, yaw=0):
    return RawMotionBatch(t, t, (RawMotionFrame(t, BIAS[0], BIAS[1]+yaw, BIAS[2], 0, 1, 0),))


def test_measured_bias_reproduces_and_fixes_idle_failure_without_hiding_turns():
    old, fixed = SensorPoseTracker(), SensorPoseTracker()
    fixed.set_device_bias(BIAS)
    counts = [0, 0]
    for t in range(0, 4010, 10):
        for i, tracker in enumerate((old, fixed)):
            counts[i] += tracker.feed_batch(batch(t), auto_center=True).auto_centered
    assert counts == [0, 1]
    assert abs(old._pose.offset_x) > 80
    assert abs(fixed._pose.offset_x) < .01
    # Compensation is fixed, not continuously relearned from slow hand motion.
    for t in range(4010, 9010, 10):
        pose = fixed.feed_batch(batch(t, yaw=3), auto_center=True)
        assert not pose.auto_centered
    assert pose.offset_x == pytest.approx(15/25*190, abs=1)
    centered = False
    for t in range(9010, 12310, 10):
        centered |= fixed.feed_batch(batch(t), auto_center=True).auto_centered
    assert centered
    assert fixed.gyro_bias == BIAS


def test_bias_survives_stream_restart_and_manual_pose_calibration():
    tracker = SensorPoseTracker()
    tracker.set_device_bias(BIAS)
    for t in range(0, 800, 10):
        tracker.feed_batch(batch(t))
    assert tracker.calibrate_neutral(allow_motion=True) is not None
    assert tracker.gyro_bias == BIAS
    tracker.reset()
    assert tracker.gyro_bias == BIAS
    tracker.feed_batch(batch(0))
    tracker.feed_batch(batch(5000))
    assert tracker.gyro_bias == BIAS


@pytest.mark.parametrize('values', [[1, 2], [True, 0, 0], [math.nan, 0, 0], [20, 0, 0], None])
def test_invalid_profiles_do_not_poison_pose(tmp_path, values):
    path = tmp_path/'sensor-bias-profiles.json'
    path.write_text(json.dumps({'schema_version': 1, 'devices': {IDENTITY: {'gyro_bias_dps': values}}}))
    assert load_sensor_bias(path, IDENTITY) == (0, 0, 0)


def test_processor_switches_bias_by_device_and_does_not_modify_raw_motion(tmp_path):
    path = tmp_path/'sensor-bias-profiles.json'
    path.write_text(json.dumps({'schema_version': 1, 'devices': {IDENTITY: {'gyro_bias_dps': BIAS}}}))
    frames_seen = []
    engine = SimpleNamespace(feed_batch=lambda b: frames_seen.append(b))
    processor = GuiEventProcessor(Settings(), threading.Event(), lambda *_: None,
                                  motion_engine=engine, mounting_path=tmp_path/'mounting-profile.json')
    processor.select_sensor_device(IDENTITY.lower())
    assert processor._sensor_pose.gyro_bias == BIAS
    source = batch(10)
    asyncio.run(processor.handle(source))
    assert frames_seen == [source]
    assert frames_seen[0] is source
    processor.select_sensor_device('OTHER-ESP32')
    assert processor._sensor_pose.gyro_bias == (0, 0, 0)
    processor.select_sensor_device(IDENTITY)
    assert processor._sensor_pose.gyro_bias == BIAS


def test_connection_selects_device_before_subscribing(monkeypatch):
    from codex_whip import ble_client
    order = []
    class Peripheral:
        is_connected = True
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def start_notify(self, *_): order.append('subscribe')
        async def stop_notify(self, *_): pass
        async def write_gatt_char(self, *args, **kwargs): pass
    monkeypatch.setattr(ble_client, 'BleakClient', lambda *a, **k: Peripheral())
    async def run():
        stop = asyncio.Event()
        stop.set()
        client = BleWhipClient(BleSettings(), device_handler=lambda identity: order.append(identity))
        async def receive(_): pass
        await client._run_connection(SimpleNamespace(address=IDENTITY), receive, stop)
    asyncio.run(run())
    assert order == [IDENTITY, 'subscribe']
