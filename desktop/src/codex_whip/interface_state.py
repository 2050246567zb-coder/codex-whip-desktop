"""Presentation-only preferences and meters; never changes detection profiles."""
from __future__ import annotations

import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import AudioChunk
from .voice import decode_ima_adpcm_chunk


@dataclass
class InterfacePreferences:
    setup_complete: bool = False
    reduce_motion: bool = False

    @classmethod
    def load(cls, path: Path, *, already_calibrated: bool) -> "InterfacePreferences":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return cls(value.get("setup_complete") is True,
                       value.get("reduce_motion") is True)
        except (OSError, ValueError, AttributeError):
            # Existing calibrated users do not need to repeat first-use setup.
            return cls(setup_complete=already_calibrated)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        temporary.replace(path)


def audio_display_level(chunk: AudioChunk) -> float:
    """Read-only RMS meter, independently decoded from the recorded ADPCM chunk."""
    try:
        pcm = decode_ima_adpcm_chunk(chunk)
        samples = tuple(v[0] for v in struct.iter_unpack("<h", pcm))
        if not samples:
            return 0.0
        rms = math.sqrt(sum(v * v for v in samples) / len(samples)) / 32768
        db = 20 * math.log10(max(rms, 1e-9))
        return min(1.0, max(0.0, (db + 60) / 54))
    except (ValueError, IndexError, struct.error):
        return 0.0
