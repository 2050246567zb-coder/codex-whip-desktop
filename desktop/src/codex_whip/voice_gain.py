"""Fixed per-recording gain with peak headroom, not a silence-chasing AGC."""
from array import array
import math
import sys


def apply_recording_gain(pcm: bytes, gain: float) -> bytes:
    if type(gain) not in (int, float) or not math.isfinite(gain) or not 1 <= gain <= 8 or len(pcm) % 2:
        raise ValueError('录音增益必须在 1–8 倍，音频须为 PCM16')
    if not pcm or gain == 1:
        return pcm
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    peak = max(abs(v) for v in samples)
    # Keep the existing cloud digital-silence gate meaningful. Do not turn
    # near-zero noise into speech candidates just by multiplying it.
    if peak < 16:
        return pcm
    effective = min(gain, 30000.0 / peak)
    amplified = array('h', (round(v * effective) for v in samples))
    if sys.byteorder != 'little':
        amplified.byteswap()
    return amplified.tobytes()
