"""Persistent PCB-to-pointing relationship, not a persistent world heading."""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import user_data_dir


@dataclass(frozen=True, slots=True)
class MountingProfile:
    # Learned with the handle approximately horizontal and pointing forward.
    # Stored in PCB coordinates; gravity supplies the vertical at each recenter.
    forward: tuple[float, float, float]
    right_angle_deg: float
    up_angle_deg: float
    schema_version: int = 1
    yaw_sign: int = 1

    def validated(self) -> MountingProfile:
        values = (*self.forward, self.right_angle_deg, self.up_angle_deg)
        limits = (14, 65) if self.schema_version == 1 else (10, 100)
        if (self.schema_version not in (1, 2) or len(self.forward) != 3
                or any(type(v) not in (float, int) or not math.isfinite(v) for v in values)
                or abs(math.sqrt(sum(v*v for v in self.forward)) - 1) > 0.01
                or type(self.yaw_sign) is not int or self.yaw_sign not in (-1, 1)
                or (self.schema_version == 1 and self.yaw_sign != 1)
                or not limits[0] <= self.right_angle_deg <= limits[1]
                or not limits[0] <= self.up_angle_deg <= limits[1]):
            raise ValueError("手柄方向档案无效，请重新做方向校准。")
        return self


def default_mounting_path() -> Path:
    return user_data_dir() / "mounting-profile.json"


def load_mounting_profile(path: Path) -> MountingProfile | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return MountingProfile(
            tuple(data["forward"]), data["right_angle_deg"],
            data["up_angle_deg"], data["schema_version"], data.get("yaw_sign", 1),
        ).validated()
    except (OSError, ValueError, TypeError, KeyError):
        return None


def save_mounting_profile(profile: MountingProfile, path: Path) -> None:
    payload = json.dumps(asdict(profile.validated()), ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".mounting-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
