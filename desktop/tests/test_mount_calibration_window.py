import tkinter as tk
from unittest.mock import Mock
import pytest

from codex_whip.gui import CodexWhipWindow, GuiEventProcessor
from codex_whip.mount_calibration_window import MountCalibrationWindow
from codex_whip.mount_profile import MountingProfile, save_mounting_profile
from codex_whip.sensor_pose import GripStability, SensorPose
from codex_whip.settings import Settings


@pytest.fixture(scope="module")
def tk_host():
    # One Tcl/Tk interpreter per module, independent test Toplevels. Creating
    # multiple roots while old StringVar callbacks are collected is unreliable.
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_wizard_steps_keyboard_controls_and_review_gate(tk_host):
    root = tk.Toplevel(tk_host)
    root.geometry("760x680+10000+10000")
    calls, closed = [], []
    window = MountCalibrationWindow(root, lambda *args: calls.append(args), lambda: closed.append(True))
    window.window.geometry("650x670+10000+10000")
    try:
        root.update()
        for stage in window.STEPS:
            window.update_state({"stage": stage, "detail": "请按提示操作"})
            root.update()
            assert window.primary.winfo_ismapped()
            assert window.primary.winfo_y() + window.primary.winfo_height() <= window.controls.winfo_height()
        assert " / 4" in window.step_label.cget("text")
        window.show_stability(GripStability(True, "可以记录 · 允许轻微手抖"))
        assert "可以记录" in window.stability_label.cget("text")
        window.update_state({"stage": "right_capture"})
        window.show_stability(GripStability(True, "可以记录"))
        assert "正在采集" in window.stability_label.cget("text")
        assert "没有倒计时" in window.stability_label.cget("text")
        assert "动作完成就能录入" in window.stability_label.cget("text")
        window.show_stability(GripStability(False, "采样有中断"))
        assert "采样有中断" in window.stability_label.cget("text")
        assert window.stage == "right_capture"
        window.update_state({"stage": "review"})
        assert window.primary.cget("text") == "归中并试方向"
        window.primary.invoke()
        assert calls[-1] == ("center", window.token)
        window.update_state({"stage": "review", "centered": True})
        assert str(window.primary["state"]) == "normal"
        window.show_pose(SensorPose(100, 40, 0, 0))
        assert len(window.preview.find_all()) == 7
        window.primary.invoke()
        assert calls[-1] == ("save", window.token)
        window.close()
        window.close()
        assert calls[-1] == ("cancel", window.token)
        assert closed == [True]
    finally:
        if window.window.winfo_exists():
            window.close()
        root.destroy()


def test_main_window_first_use_reopen_and_stale_arm_result(tmp_path, monkeypatch, tk_host):
    monkeypatch.setenv("CODEX_WHIP_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("codex_whip.gui.default_mounting_path", lambda: tmp_path / "mounting-profile.json")
    monkeypatch.setattr("codex_whip.gui.CodexWhipEffects", Mock())
    for name in ("start_listening", "check_codex", "_replace_scare_hotkey", "_schedule_effect_target_refresh"):
        monkeypatch.setattr(CodexWhipWindow, name, lambda *a, **kw: None)
    root = tk.Toplevel(tk_host)
    window = CodexWhipWindow(root, Settings(), None)
    root.geometry("760x680+10000+10000")
    processor = GuiEventProcessor(Settings(), window.armed, window.emit, mounting_path=window.mounting_path)
    window.processor = processor
    window.worker_loop = Mock(call_soon_threadsafe=lambda fn, *a: fn(*a))
    try:
        window.ble_connected = True
        window.emit("sensor_pose", SensorPose(0, 0, 0, 0))
        window._drain_events()
        assert window.mount_window is None  # Connect first; user chooses to begin.
        window.ui.stage = "tour"
        window.ui._tour_index = len(window.ui.TOUR) - 1
        window.ui.advance()
        window._drain_events()
        assert window.mount_window is None
        assert processor._mount_session is not None
        assert window.ui.stage == "calibrate"
        assert window.ui._mount_inline_state == "neutral"
        old_generation = window._arm_generation - 1
        token = window.ui._mount_token
        window._send_mount_command("cancel", token)
        window._drain_events()
        window.emit("arm_result", (True, {"handle": 1, "pid": 1}, old_generation))
        window.emit("sensor_pose", SensorPose(0, 0, 0, 0))
        window._drain_events()
        assert window.mount_window is None  # Skip lasts for this launch.
        assert not window.armed.is_set()
        assert not window.mounting_path.exists()
        window.calibrate_sensor_neutral()
        assert window.ui.stage == "calibrate"  # Main button can reopen in place.
        window._send_mount_command("cancel", window.ui._mount_token)
        window._drain_events()
        save_mounting_profile(MountingProfile((0, 0, 1), 30, 30), window.mounting_path)
    finally:
        window.close()

    root = tk.Toplevel(tk_host)
    next_launch = CodexWhipWindow(root, Settings(), None)
    try:
        assert next_launch._mount_prompted  # No forced onboarding for saved users.
        assert next_launch.sensor_calibrate_button.cget("text") == "开始校准"
    finally:
        next_launch.close()
