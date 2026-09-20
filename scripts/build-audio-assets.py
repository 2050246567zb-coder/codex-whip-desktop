from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "desktop" / "assets" / "audio" / "source"
OUTPUT = ROOT / "desktop" / "assets" / "audio" / "whip_strike.wav"
SAMPLE_RATE = 44_100


def read_pcm(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as audio:
        if (
            audio.getnchannels() != 1
            or audio.getsampwidth() != 2
            or audio.getframerate() != SAMPLE_RATE
        ):
            raise ValueError(f"{path.name} must be mono 16-bit PCM at {SAMPLE_RATE} Hz")
        raw = audio.readframes(audio.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def build() -> None:
    whip = read_pcm(SOURCE / "Whip-sound.wav")
    thud = read_pcm(SOURCE / "Dull_thud.wav")

    crack_index = max(range(len(whip)), key=lambda index: abs(whip[index]))
    crop_start = max(0, crack_index - round(0.220 * SAMPLE_RATE))
    crop_end = min(len(whip), crack_index + round(0.350 * SAMPLE_RATE))
    whip = whip[crop_start:crop_end]
    crack_index -= crop_start

    thud_peak = max(range(len(thud)), key=lambda index: abs(thud[index]))
    # Let the shock-like crack arrive first, followed by the object's body impact.
    thud_start = max(0, crack_index + round(0.020 * SAMPLE_RATE) - thud_peak)
    frame_count = max(len(whip), thud_start + len(thud))
    mixed: list[int] = []
    for index in range(frame_count):
        whip_value = whip[index] * 0.86 if index < len(whip) else 0.0
        thud_index = index - thud_start
        thud_value = thud[thud_index] * 0.55 if 0 <= thud_index < len(thud) else 0.0
        value = (whip_value + thud_value) / 32768.0
        # A gentle soft limiter prevents digital clipping without rounding off
        # the first pressure spike as aggressively as hard normalization.
        limited = math.tanh(value * 1.10) / math.tanh(1.10)
        mixed.append(round(max(-0.985, min(0.985, limited)) * 32767))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUTPUT), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(SAMPLE_RATE)
        audio.writeframes(struct.pack(f"<{len(mixed)}h", *mixed))
    print(
        f"built {OUTPUT} ({len(mixed) / SAMPLE_RATE:.3f}s, "
        f"crack at {crack_index / SAMPLE_RATE:.3f}s)"
    )


if __name__ == "__main__":
    build()
