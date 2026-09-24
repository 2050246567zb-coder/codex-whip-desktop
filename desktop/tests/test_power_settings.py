import asyncio
import threading
from unittest.mock import Mock

import pytest

from codex_whip.power_settings import PowerSettings, supports_power_saving
from codex_whip.gui import CodexWhipWindow, GuiEventProcessor
from codex_whip.models import DeviceMessage, WhipEvent
from codex_whip.settings import Settings
from codex_whip.motion_v3 import MotionEngine


@pytest.mark.parametrize('version, expected', [('0.6.0',False),('0.6.1',True),
    ('0.6.2',True),('0.7.7',False),('0.8.1',True),('0.8.2',True),
    ('0.9.0',False),('bad',False),('0.6',False)])
def test_capability_is_not_accidentally_enabled_on_c3(version, expected):
    assert supports_power_saving(version) == expected


def test_preference_is_opt_in_persistent_and_strict(tmp_path):
    path = tmp_path/'power.json'
    value = PowerSettings(path)
    assert value.enabled is False
    value.save(True)
    assert PowerSettings(path).command() == 'POWER,1'
    value.save(False)
    assert PowerSettings(path).command() == 'POWER,0'
    for bad in ('null','[]','{"enabled":"true"}','broken'):
        path.write_text(bad)
        assert PowerSettings(path).enabled is False


@pytest.mark.parametrize('connected,version,expected', [
    (False,'0.6.1',None),(True,'0.6.0',None),(True,'0.7.7',None),
    (True,'0.6.1','POWER,1'),(True,'0.8.2','POWER,1')])
def test_apply_only_syncs_capable_connected_board(tmp_path,connected,version,expected):
    app=object.__new__(CodexWhipWindow)
    app.power_store=PowerSettings(tmp_path/'power.json')
    app.ble_connected=connected
    app.firmware_version=version
    app._set_power_status=Mock()
    app.send_device_command=Mock(return_value=True)
    assert app.apply_power_settings(True)
    if expected:
        app.send_device_command.assert_called_once_with(expected)
    else:
        app.send_device_command.assert_not_called()
    assert PowerSettings(app.power_store.path).enabled


def test_sleep_wake_resets_buffers_but_preserves_bias_and_ignores_pickup(tmp_path):
    emitted=[]
    engine=MotionEngine(tmp_path/'motion.json')
    processor=GuiEventProcessor(Settings(),threading.Event(),lambda *v:emitted.append(v),motion_engine=engine)
    processor._sensor_pose.set_device_bias((1.,-3.,0.))
    engine._active_since=10
    engine._frames.append(object())
    async def run():
        await processor.handle(DeviceMessage('POWER',('1','SLEEP','300'),''))
        assert not engine._frames
        assert engine._active_since is None
        await processor.handle(DeviceMessage('POWER',('1','ACTIVE','300'),''))
        await processor.handle(WhipEvent(1,980.,3.2,120))
    asyncio.run(run())
    assert processor._sensor_pose.gyro_bias==(1.,-3.,0.)
    assert all(kind not in ('whip','send_result') for kind,_ in emitted)


def test_stream_reset_preserves_learning_pause(tmp_path):
    engine=MotionEngine(tmp_path/'motion.json')
    engine.set_paused(True)
    profile=engine.profile
    engine.reset_stream()
    assert engine._paused and engine.profile is profile


def test_repeated_power_ack_does_not_reset_pose_or_extend_gesture_guard(tmp_path):
    processor=GuiEventProcessor(Settings(),threading.Event(),lambda *_:None)
    async def run():
        await processor.handle(DeviceMessage('POWER',('1','ACTIVE','300'),''))
        processor._motion_resume_at = 0
        processor._sensor_pose.reset = Mock()
        await processor.handle(DeviceMessage('POWER',('0','ACTIVE','300'),''))
        assert processor._motion_resume_at == 0
        processor._sensor_pose.reset.assert_not_called()
    asyncio.run(run())


def test_failed_preference_write_preserves_previous_state(tmp_path, monkeypatch):
    from pathlib import Path
    store=PowerSettings(tmp_path/'power.json')
    monkeypatch.setattr(Path,'replace',Mock(side_effect=OSError('disk full')))
    with pytest.raises(OSError):
        store.save(True)
    assert not store.enabled


@pytest.mark.parametrize('exists, expected', [(True,True),(False,False)])
def test_closed_calibration_window_cannot_keep_board_awake(exists,expected):
    from types import SimpleNamespace
    app=object.__new__(CodexWhipWindow)
    app.settings_window=SimpleNamespace(window=Mock(winfo_exists=lambda:exists),_stage='positive')
    app.mount_window=None
    app.voice_module=SimpleNamespace(calibration_active=False)
    app.ble_connected=True
    app.firmware_version='0.6.1'
    app.send_device_command=Mock()
    app._keep_awake_for_calibration()
    assert app.send_device_command.called == expected


def test_inline_direction_calibration_keeps_controller_awake():
    from types import SimpleNamespace
    app = object.__new__(CodexWhipWindow)
    app.ui = SimpleNamespace(stage='calibrate')
    app.settings_window = None
    app.mount_window = None
    app.voice_module = SimpleNamespace(calibration_active=False)
    app.ble_connected = True
    app.firmware_version = '0.6.1'
    app.send_device_command = Mock()
    app._keep_awake_for_calibration()
    app.send_device_command.assert_called_once_with('POWERHOLD')
