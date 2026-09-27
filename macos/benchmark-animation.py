"""Measure local Tk home-animation cadence with synthetic pose data.

No Bluetooth connection, Codex input, or native overlay is used.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time
import tkinter as tk
from pathlib import Path

from codex_whip.effects import CodexWhipEffects
from codex_whip.interface import BG, Hero


class MeasuredHero(Hero):
    def __init__(self, *args, **kwargs):
        self.frame_starts: list[float] = []
        self.frame_costs: list[float] = []
        super().__init__(*args, **kwargs)

    def _draw(self):
        started = time.perf_counter()
        super()._draw()
        self.frame_starts.append(started)
        self.frame_costs.append((time.perf_counter() - started) * 1000)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round((len(ordered) - 1) * fraction))]


def measure(hero: MeasuredHero, root: tk.Tk, mode: str, seconds: float) -> dict:
    hero.set_mode(mode)
    warmup_end = time.perf_counter() + 0.5
    end = warmup_end + seconds
    next_audio = 0.0
    hero.frame_starts.clear()
    hero.frame_costs.clear()
    while time.perf_counter() < end:
        now = time.perf_counter()
        if mode == "recording" and now >= next_audio:
            hero.audio_level((math.sin(now * 12) + 1) / 2)
            next_audio = now + 0.05
        root.update()
        time.sleep(0.001)
    starts = [value for value in hero.frame_starts if value >= warmup_end]
    costs = hero.frame_costs[-len(starts):]
    gaps = [(right - left) * 1000 for left, right in zip(starts, starts[1:])]
    return {
        "mode": mode,
        "duration_seconds": seconds,
        "frames": len(starts),
        "fps": round((len(starts) - 1) / (starts[-1] - starts[0]), 1),
        "frame_gap_p50_ms": round(statistics.median(gaps), 2),
        "frame_gap_p95_ms": round(percentile(gaps, .95), 2),
        "frame_gap_max_ms": round(max(gaps), 2),
        "gaps_over_33ms": sum(gap > 33.3 for gap in gaps),
        "draw_cost_p95_ms": round(percentile(costs, .95), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=3.0)
    args = parser.parse_args()
    root = tk.Tk()
    root.title("Codex Whip 动画性能测试")
    root.geometry("560x660+100+100")
    root.configure(bg=BG)
    started = time.perf_counter()

    def frame():
        now = time.perf_counter() - started
        return CodexWhipEffects.IDLE, (.24 * math.sin(now * 2.0),
                                       .18 * math.cos(now * 1.6))

    hero = MeasuredHero(root, size=275, frame_provider=frame,
                        interactive=False, high_resolution=True)
    hero.pack(fill="both", expand=True, padx=28, pady=20)
    report = {"synthetic": True, "native_overlay_tested": False,
              "scenes": []}
    try:
        for mode in ("connecting", "whip", "away", "recording", "recognizing"):
            report["scenes"].append(measure(hero, root, mode, args.seconds))
    finally:
        hero.close()
        root.destroy()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
