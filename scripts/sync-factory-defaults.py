"""Snapshot approved product defaults from the current Windows user profile.

Only the explicit allowlist is public. Device identity, sensor bias, recordings,
logs, UI position, and first-use completion never enter the factory bundle.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from codex_whip.interface_state import InterfacePreferences
from codex_whip.messages import MessageProfile
from codex_whip.paths import user_data_dir
from codex_whip.visual_settings import VISUAL_SETTINGS_SCHEMA_VERSION
from codex_whip.voice import VOICE_SETTINGS_SCHEMA, load_voice_settings


ROOT = Path(__file__).resolve().parents[1]
FACTORY = ROOT / "desktop" / "assets" / "factory-calibration"
APPROVED_PROFILES = (
    "detector-profile.json",
    "double-tap-profile-v2.json",
    "mounting-profile.json",
    "whip-sensitivity.json",
)


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def snapshot(source: Path = user_data_dir(), target: Path = FACTORY) -> None:
    payloads: dict[str, bytes] = {}
    for name in APPROVED_PROFILES:
        payloads[name] = (source / name).read_bytes()
        json.loads(payloads[name])

    voice = load_voice_settings(source / "voice-settings.json")
    payloads["voice-settings.json"] = encoded(
        {"schema_version": VOICE_SETTINGS_SCHEMA, **asdict(voice)}
    )

    interface = InterfacePreferences.load(
        source / "interface-preferences.json", already_calibrated=False
    )
    payloads["interface-preferences.json"] = encoded(
        {"setup_complete": False,
         "reduce_motion": interface.reduce_motion,
         "send_enabled": interface.send_enabled}
    )

    power = json.loads((source / "power-settings.json").read_text(encoding="utf-8"))
    payloads["power-settings.json"] = encoded({"enabled": power["enabled"] is True})

    visual = json.loads((source / "visual-settings.json").read_text(encoding="utf-8"))
    payloads["visual-settings.json"] = encoded(
        {"schema_version": VISUAL_SETTINGS_SCHEMA_VERSION,
         "strikes_per_wound": int(visual["strikes_per_wound"]),
         "wounds_enabled": visual["wounds_enabled"] is True,
         "sound_enabled": visual["sound_enabled"] is True,
         "scare_enabled": False}
    )

    message = json.loads((source / "message-profile.json").read_text(encoding="utf-8"))
    profile = MessageProfile(message["order"], tuple(message["messages"])).validated()
    payloads["message-profile.json"] = encoded(
        {"schema_version": 1, "order": profile.order,
         "messages": list(profile.messages)}
    )

    target.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, data in sorted(payloads.items()):
        (target / name).write_bytes(data)
        entries.append({"path": name, "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {"schema_version": 1, "kind": "factory-defaults",
                "hardware": "XIAO nRF52840 Sense / LSM6DS3TR-C",
                "files": entries}
    (target / "migration-manifest.json").write_bytes(encoded(manifest))


if __name__ == "__main__":
    snapshot()
