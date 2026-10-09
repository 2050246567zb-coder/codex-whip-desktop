"""Isolated, synthetic UI smoke test; never connects BLE or sends to Codex."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import ssl
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import Mock, patch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    from .gui import CodexWhipWindow
    from .effects import CodexWhipEffects
    from .sensor_pose import SensorPose
    from .settings import Settings
    from .migration import FACTORY_DEFAULT_FILES, import_factory_calibration_once
    from .interface_state import initialize_fresh_preferences, InterfacePreferences
    from .cloud_speech import bundled_ca_path
    from .voice import WhisperCppTranscriber
    from PIL import ImageGrab

    report = {"synthetic": True, "native_overlay_tested": False,
              "screenshots": [], "capture_errors": [], "callback_errors": [],
              "high_rate_event_buffer_drained": False, "factory_defaults_loaded": False,
              "bundled_https_ca": False, "offline_model_ready": False,
              "settings_wheel_scroll": False, "settings_touchpad_scroll": False,
              "settings_wheel_step_down": False, "settings_wheel_step_up": False,
              "codex_connection_status": False,
              "new_user_tour_and_calibration": False}
    with tempfile.TemporaryDirectory(prefix="codex-whip-ui-") as data, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"CODEX_WHIP_DATA_DIR": data}))
        ca = bundled_ca_path()
        if not ca.is_file():
            raise RuntimeError("Packaged HTTPS CA certificate store is missing")
        ssl.create_default_context(cafile=str(ca))
        report["bundled_https_ca"] = True
        local = WhisperCppTranscriber(Path(data) / "voice")
        with patch("urllib.request.urlopen", side_effect=AssertionError("offline model accessed network")):
            local.prepare()
        report["offline_model_ready"] = local.ready
        if not report["offline_model_ready"]:
            raise RuntimeError("Packaged Whisper model is not ready offline")
        initialize_fresh_preferences(Path(data))
        factory = import_factory_calibration_once()
        report["factory_defaults_loaded"] = (
            set(factory.imported) == FACTORY_DEFAULT_FILES and not factory.errors
        )
        if not report["factory_defaults_loaded"]:
            raise RuntimeError("Packaged factory defaults are missing or invalid")
        if InterfacePreferences.load(
            Path(data) / "interface-preferences.json", already_calibrated=True
        ).setup_complete:
            raise RuntimeError("New-user tour was incorrectly marked complete")
        fake_effects = Mock()
        fake_effects.preview_frame.return_value = (CodexWhipEffects.IDLE, (0., 0.))
        fake_effects.target_active.return_value = True
        stack.enter_context(patch("codex_whip.gui.CodexWhipEffects", return_value=fake_effects))
        for method in ("start_listening", "check_codex", "_replace_scare_hotkey",
                       "_schedule_effect_target_refresh"):
            stack.enter_context(patch.object(CodexWhipWindow, method, lambda *a, **kw: None))
        root = tk.Tk()
        root.report_callback_exception = lambda typ, val, tb: report["callback_errors"].append(str(val))
        app = CodexWhipWindow(root, Settings(), None)
        if app.ui.stage != "connect":
            raise RuntimeError("New-user guide did not open at the connection step")
        app.ble_connected = True
        app.worker_loop = Mock()
        app.processor = Mock()
        app.ui.observe("sensor_pose", SensorPose(0.0, 0.0, 0.0, 0.0, True))
        app.ui._refresh()
        if app.ui.stage != "tour":
            raise RuntimeError("Connected new user did not enter the feature tour")
        app.ui._tour_index = len(app.ui.TOUR) - 1
        with patch.object(app, "_send_mount_command"):
            app.ui.next_tour()
        report["new_user_tour_and_calibration"] = app.ui.stage == "calibrate"
        if not report["new_user_tour_and_calibration"]:
            raise RuntimeError("Feature tour did not open direction calibration")
        root.geometry("560x660+80+80")
        app.ui.stage = "ready"
        app.ui._mount_token = ""
        app._restore_send_state()

        def settle_and_capture(name, delay=1.2, widget=None):
            until = time.monotonic() + delay
            while time.monotonic() < until:
                root.update()
                time.sleep(.008)
            try:
                target = widget or root
                target.lift()
                target.attributes("-topmost", True)
                root.update()
                x, y = target.winfo_rootx(), target.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + target.winfo_width(), y + target.winfo_height())).save(args.output / f"{name}.png")
                target.attributes("-topmost", False)
                report["screenshots"].append(name)
            except Exception as exc:
                report["capture_errors"].append(f"{name}: {exc}")

        try:
            settle_and_capture("01-connecting")
            app.ble_connected = True
            app.worker_loop = Mock()
            app.ui.observe("battery", {"percent": 68, "charging": False})
            settle_and_capture("02-whip")
            fake_effects.target_active.return_value = False
            settle_and_capture("02-away", .35)
            fake_effects.target_active.return_value = True
            settle_and_capture("02-return-to-codex", .35)
            app.ui.stage = "tour"
            app.ui._tour_index = 0
            app.ui._render_key = None
            settle_and_capture("02c-tour-auto-center")
            app.ui._tour_index = next(
                i for i, item in enumerate(app.ui.TOUR) if item[0] == "pending"
            )
            app.ui._tour_started = time.monotonic()
            app.ui._render_key = None
            settle_and_capture("02d-tour-countdown")
            app.ui._tour_index = len(app.ui.TOUR) - 1
            app.ui._render_key = None
            settle_and_capture("02e-tour-final")
            clock_started = app.ui.hero._clock_started
            app.ui.hero._outer_motion(SimpleNamespace(x_root=0, y_root=0))
            if not app.ui.hero._clock_hover or app.ui.hero._clock_started != clock_started:
                report["callback_errors"].append("tour clock changed after outside pointer motion")
            settle_and_capture("02e-tour-clock-stable", .35)
            app.ui.stage = "calibrate"
            app.ui._mount_token = "synthetic"
            app.ui._mount_inline_state = "up_ready"
            app.ui._render_key = None
            settle_and_capture("02f-calibrate-up")
            app.ui._mount_inline_state = "right_ready"
            app.ui._render_key = None
            settle_and_capture("02g-calibrate-right")
            app.ui.stage = "ready"
            app.ui._mount_token = ""
            app.ui._render_key = None
            app.ui.open_preferences("calibration")
            app.ui.settings.geometry("1060x820+100+40")
            settle_and_capture("02a-hardware-tap-settings", .3, app.ui.settings)
            if root.state() != "withdrawn" or app.ui.settings.state() != "normal":
                report["callback_errors"].append("home and settings window visibility overlap")
            canvas = app.ui._advanced_canvas
            for sequence, delta, key in (("<MouseWheel>", -120, "settings_wheel_scroll"),
                                          ("<TouchpadScroll>", 65516, "settings_touchpad_scroll")):
                canvas.yview_moveto(0)
                app.arm_check.event_generate(sequence, delta=delta)
                root.update_idletasks()
                report[key] = canvas.yview()[0] > 0
                if not report[key]:
                    report["callback_errors"].append(f"{key} failed over settings control")
            for delta, key in ((-120, "settings_wheel_step_down"),
                               (120, "settings_wheel_step_up")):
                canvas.yview_moveto(.3)
                before = canvas.canvasy(0)
                app.arm_check.event_generate("<MouseWheel>", delta=delta)
                root.update_idletasks()
                report[key] = abs(canvas.canvasy(0) - before + delta / 120 * 40) <= 1
                if not report[key]:
                    report["callback_errors"].append(f"{key} jumped instead of scrolling one notch")
            app.emit("effect_target_auto", (True, {"handle": 42, "pid": 42}))
            app.emit("codex_result", (False, "synthetic composer not ready"))
            app._drain_events()
            report["codex_connection_status"] = app.codex_value.get() == "已连接"
            if not report["codex_connection_status"]:
                report["callback_errors"].append("window discovery did not update Codex connection status")
            canvas.yview_moveto(0)
            app.settings_window._start_message_drag(0, SimpleNamespace())
            settle_and_capture("02a-message-drag", .2, app.ui.settings)
            card = app.settings_window._message_cards[0]
            app.settings_window._finish_message_drag(SimpleNamespace(
                x_root=card.winfo_rootx() + 5,
                y_root=card.winfo_rooty() + card.winfo_height() // 2))
            app.ui._advanced_canvas.yview_moveto(.31)
            settle_and_capture("02a-voice-settings", .2, app.ui.settings)
            app.ui._advanced_canvas.yview_moveto(.43)
            settle_and_capture("02a-settings-middle", .2, app.ui.settings)
            app.ui._advanced_canvas.yview_moveto(1.0)
            settle_and_capture("02a-hardware-tap-settings-bottom", .2, app.ui.settings)
            app.ui.hide_preferences()
            app.ui.observe("battery", {"percent": 18, "charging": False})
            settle_and_capture("02b-battery-low", .2)
            app.ui.observe("battery", {"percent": 68, "charging": True})
            settle_and_capture("02c-battery-charging", .2)
            app.ui.observe("battery", {"percent": 68, "charging": False})
            app.ui.hero._set_clock(True)
            settle_and_capture("03-clock")
            app.ui.hero._set_clock(False)
            settle_and_capture("04-return-to-whip")
            from .models import DeviceMessage
            app.ui.observe('device', DeviceMessage('POWER', ('1', 'SLEEP', '300'), ''))
            settle_and_capture('04b-deep-sleep', 1.1)
            app.ui.observe('device', DeviceMessage('POWER', ('1', 'ACTIVE', '300'), ''))
            settle_and_capture('04c-wake', .5)
            # Reproduce the live recording load: RAW pose and audio meter
            # notifications arrive concurrently and used to keep Tk's drain
            # loop busy forever.  Only the newest display frames should remain.
            for value in range(10_000):
                app.emit("sensor_pose", SensorPose(0.1, -0.1, 5.0, 0.4, True))
                app.emit("ui_audio_level", value / 10_000)
            app.emit("voice_state", {"state": "recording"})
            settle_and_capture("05-recording")
            report["high_rate_event_buffer_drained"] = app.events.empty()
            if not report["high_rate_event_buffer_drained"]:
                report["callback_errors"].append("high-rate UI event buffer did not drain")
            app.ui._voice_state = "recognizing"
            settle_and_capture("06-recognizing")
            app.ui.observe('voice_pending', '请继续完成界面和动画测试')
            app.ui.observe('voice_state', {'state':'ready'})
            settle_and_capture('07-voice-pending')
            settle_and_capture('07b-countdown-half',3.8)
            settle_and_capture('07c-countdown-near-end',3.8)
            settle_and_capture('07d-expired',2.1)
            app.ui.observe('voice_pending', '')
            settle_and_capture('08-after-send')
        finally:
            app.close()
            (args.output / "ui-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["callback_errors"] else 0
