"""One strength control, relative to a stable user baseline (never compounded)."""
import json
import math
from dataclasses import asdict, replace
from pathlib import Path

from .calibration import DetectorProfile
from .paths import user_data_dir


def scaled_profile(base: DetectorProfile, percent: float) -> DetectorProfile:
    if not math.isfinite(percent) or not 60 <= percent <= 180:
        raise ValueError('灵敏度必须在 60% 到 180% 之间')
    factor = 100 / percent
    bounds = {
        'start_gyro_dps': (100, 2500), 'confirm_gyro_dps': (150, 3500),
        'start_dynamic_accel_g': (.1, 8), 'confirm_dynamic_accel_g': (.1, 12),
        'hard_dynamic_accel_g': (.2, 15), 'hard_accel_min_gyro_dps': (100, 3000),
        'minimum_angular_travel_deg': (5, 500),
    }
    values = {key: round(max(low, min(high, getattr(base, key)*factor)), 4)
              for key, (low, high) in bounds.items()}
    # Keep shape, duration, quiet-tail and confirmation-frame protections.
    return replace(base, **values).validated()


class WhipSensitivity:
    def __init__(self, current: DetectorProfile, path: Path | None = None):
        self.path = path or user_data_dir() / 'whip-sensitivity.json'
        self.base, self.percent = current, 100.0
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            base = DetectorProfile(**data['base']).validated()
            percent = float(data['percent'])
            if scaled_profile(base, percent) == current:
                self.base, self.percent = base, percent
        except (OSError, ValueError, TypeError, KeyError):
            pass

    def save(self, percent: float):
        scaled_profile(self.base, percent)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'base': asdict(self.base), 'percent': percent},
                                       ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)
        self.percent = percent
