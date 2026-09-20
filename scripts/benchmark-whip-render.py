"""Offline render-only benchmark. No BLE, audio, Codex discovery or sending.

Compare old/new dual-view canvas drawing, not monitor-presented FPS.
"""
import json
from pathlib import Path
import statistics
import time
import tkinter as tk
import types
import zipfile

from codex_whip.effects import CartoonWhipPhysics, CodexWhipEffects
from codex_whip.whip_drawing import WhipDrawing

root = tk.Tk()
root.geometry("900x700+10000+10000")
root.title("Offline whip render benchmark")
root.update()
source = Path(__file__).resolve().parents[1] / "backups/before-2.2.2-ui/source.zip"
with zipfile.ZipFile(source) as archive:
    name = next(name for name in archive.namelist() if name.endswith("codex_whip/whip_drawing.py"))
    old = types.ModuleType("codex_whip.old_whip_drawing")
    old.__package__ = "codex_whip"
    exec(compile(archive.read(name), name, "exec"), old.__dict__)

physics = CartoonWhipPhysics(CodexWhipEffects.IDLE.handle_start)
poses = [physics.step((580 + i % 80, 480 + i % 35), 1/60,
                       aim_offset_degrees=i % 30, kinematic=True) for i in range(240)]
try:
    results = {}
    for name, renderer in (("2.2.1", old.WhipDrawing), ("2.2.2", WhipDrawing)):
        canvas = tk.Canvas(root, width=1200, height=1200)
        canvas.pack()
        overlay, preview = renderer(canvas), renderer(canvas)
        times = []
        for pose in poses:
            start = time.perf_counter()
            overlay.draw(pose)
            preview.draw(pose, scale=.3, offset=(20, 30))
            root.update_idletasks()
            times.append((time.perf_counter()-start)*1000)
        results[name] = {"mean_ms": round(statistics.mean(times[20:]), 3),
                         "p95_ms": round(sorted(times[20:])[208], 3)}
        canvas.destroy()
    print(json.dumps(results, indent=2))
finally:
    root.destroy()
