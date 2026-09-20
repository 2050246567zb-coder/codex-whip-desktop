"""Offline UI fixture: no BLE, microphone, overlay, hotkeys or message sending.

F1 connection, F2 home, F3 recording, F4 preferences, F5 optional learning,
F6 direction wizard, F7 strike, F8 recognized text. For visual QA only.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
import tkinter as tk
from unittest.mock import Mock, patch

os.environ["CODEX_WHIP_DATA_DIR"] = tempfile.mkdtemp(prefix="codex-whip-ui-preview-")

from codex_whip.gui import CodexWhipWindow
from codex_whip.settings import Settings
from codex_whip.mount_profile import MountingProfile, save_mounting_profile
from codex_whip.mount_calibration_window import MountCalibrationWindow


def main():
    root = tk.Tk()
    patches = [patch("codex_whip.gui.CodexWhipEffects", Mock())]
    patches += [patch.object(CodexWhipWindow, name, lambda *a, **kw: None)
                for name in ("start_listening", "check_codex", "_replace_scare_hotkey",
                             "_schedule_effect_target_refresh")]
    for item in patches:
        item.start()
    app = CodexWhipWindow(root, Settings(), None)
    root.title("Codex 鞭子 · UI 预览（离线）")
    app.worker_loop = Mock()
    app.ble_value.set("界面预览 · 未连接真实设备")
    app.codex_value.set("预览不操作 Codex")
    app.firmware_supports_raw = True
    app.firmware_supports_settings = True
    app.send_device_command = lambda *_: True
    app.ble_connected = True
    ui = app.ui

    def scene(stage):
        ui.hide_preferences()
        ui.stage = stage
        ui._voice_state = ""
        ui._pending = ""
        ui._render_key = None

    def record():
        scene("ready")
        ui.observe("voice_state", {"state": "recording"})
        ui._notice = "离线视觉检查；未打开麦克风"

    def calibration():
        stages = iter(["right_ready", "right_capture", "up_ready", "up_capture", "review"])
        def command(action, _token):
            if action == "cancel":
                return
            if action == "save":
                window.close(notify=False)
                return
            window.update_state({"stage": "review" if action == "center" else next(stages, "review"),
                                 "centered": action == "center"})
        window = MountCalibrationWindow(root, command, lambda: None)
        window.update_state({"stage": "neutral"})

    root.bind("<F1>", lambda e: scene("connect"))
    root.bind("<F2>", lambda e: scene("ready"))
    root.bind("<F3>", lambda e: record())
    root.bind("<F4>", lambda e: ui.open_preferences())
    root.bind("<F5>", lambda e: scene("choices"))
    root.bind("<F6>", lambda e: calibration())
    root.bind("<F7>", lambda e: ui.hero.strike())
    root.bind("<F8>", lambda e: (scene("ready"), ui.observe("voice_pending", "先完成最关键的一步，然后告诉我验证结果。")))
    scene("ready")
    if "--settings" in sys.argv:
        section = sys.argv[sys.argv.index("--settings") + 1]
        root.after(200, lambda: ui.open_preferences(section))
    root.mainloop()
    for item in reversed(patches):
        item.stop()


if __name__ == "__main__":
    main()
