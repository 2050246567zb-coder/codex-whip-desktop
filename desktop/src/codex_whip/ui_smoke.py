"""Isolated, synthetic UI smoke test; never connects BLE or sends to Codex."""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import tempfile
import time
import tkinter as tk
from unittest.mock import Mock, patch


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    from .gui import CodexWhipWindow
    from .effects import CodexWhipEffects
    from .settings import Settings
    from PIL import ImageGrab

    report = {"synthetic": True, "native_overlay_tested": False,
              "screenshots": [], "capture_errors": [], "callback_errors": []}
    with tempfile.TemporaryDirectory(prefix="codex-whip-ui-") as data, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {"CODEX_WHIP_DATA_DIR": data}))
        fake_effects = Mock()
        fake_effects.preview_frame.return_value = (CodexWhipEffects.IDLE, (0., 0.))
        stack.enter_context(patch("codex_whip.gui.CodexWhipEffects", return_value=fake_effects))
        for method in ("start_listening", "check_codex", "_replace_scare_hotkey",
                       "_schedule_effect_target_refresh"):
            stack.enter_context(patch.object(CodexWhipWindow, method, lambda *a, **kw: None))
        root = tk.Tk()
        root.report_callback_exception = lambda typ, val, tb: report["callback_errors"].append(str(val))
        app = CodexWhipWindow(root, Settings(), None)
        root.geometry("560x660+80+80")
        app.ui.stage = "ready"

        def settle_and_capture(name):
            until = time.monotonic() + 1.2
            while time.monotonic() < until:
                root.update()
                time.sleep(.008)
            try:
                x, y = root.winfo_rootx(), root.winfo_rooty()
                ImageGrab.grab(bbox=(x, y, x + root.winfo_width(), y + root.winfo_height())).save(args.output / f"{name}.png")
                report["screenshots"].append(name)
            except Exception as exc:
                report["capture_errors"].append(f"{name}: {exc}")

        try:
            settle_and_capture("01-connecting")
            app.ble_connected = True
            app.worker_loop = Mock()
            settle_and_capture("02-whip")
            app.ui.hero._set_clock(True)
            settle_and_capture("03-clock")
            app.ui.hero._set_clock(False)
            settle_and_capture("04-return-to-whip")
            app.ui._voice_state = "recording"
            settle_and_capture("05-recording")
            app.ui._voice_state = "recognizing"
            settle_and_capture("06-recognizing")
        finally:
            app.close()
            (args.output / "ui-smoke.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["callback_errors"] else 0
