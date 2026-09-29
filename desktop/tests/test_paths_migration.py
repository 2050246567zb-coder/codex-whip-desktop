from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codex_whip import paths
from codex_whip.migration import (
    FACTORY_DEFAULT_FILES,
    bundled_factory_calibration_dir,
    import_factory_calibration_once,
    import_migration_data,
    restore_factory_defaults,
)


def _manifest_for(source: Path, relative: str, data: bytes) -> None:
    path = source / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    (source / "migration-manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "files": [
                    {
                        "path": relative,
                        "size": len(data),
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_user_data_override_is_cross_platform(monkeypatch, tmp_path: Path) -> None:
    target = tmp_path / "portable"
    monkeypatch.setenv("CODEX_WHIP_DATA_DIR", str(target))

    assert paths.user_data_dir() == target


def test_macos_data_path_uses_application_support(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CODEX_WHIP_DATA_DIR", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "darwin")
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: tmp_path))

    assert paths.user_data_dir() == tmp_path / "Library" / "Application Support" / "CodexWhip"
    assert paths.voice_runtime_dir() == paths.user_data_dir() / "voice"


def test_migration_imports_verified_file_and_preserves_existing(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _manifest_for(source, "voice/model.bin", b"verified-model")

    first = import_migration_data(source, target)
    assert first.imported == ("voice/model.bin",)
    assert not first.errors
    assert (target / "voice" / "model.bin").read_bytes() == b"verified-model"

    (target / "voice" / "model.bin").write_bytes(b"new-mac-data")
    second = import_migration_data(source, target)
    assert second.preserved == ("voice/model.bin",)
    assert (target / "voice" / "model.bin").read_bytes() == b"new-mac-data"


def test_migration_rejects_hash_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _manifest_for(source, "profile.json", b"original")
    (source / "profile.json").write_bytes(b"tampered")

    result = import_migration_data(source, target)

    assert not result.imported
    assert result.errors
    assert not (target / "profile.json").exists()


def test_factory_defaults_are_approved_and_verified() -> None:
    source = bundled_factory_calibration_dir()
    assert source is not None
    manifest = json.loads((source / "migration-manifest.json").read_text(encoding="utf-8"))
    entries = manifest["files"]
    expected = FACTORY_DEFAULT_FILES
    assert {entry["path"] for entry in entries} == expected
    for entry in entries:
        path = source / entry["path"]
        assert path.stat().st_size == entry["size"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
    combined = "\n".join((source / name).read_text(encoding="utf-8") for name in expected)
    assert "api_key" not in combined.lower()
    assert "last-voice-recording" not in combined
    assert "E2:30:F0:9F:D1:21" not in combined
    assert "sensor-bias" not in combined
    assert "ble-device-preference" not in combined
    assert "overlay-position" not in combined
    voice = json.loads((source / "voice-settings.json").read_text(encoding="utf-8"))
    assert voice["enabled"] is True
    assert voice["precise_recognition"] is True
    assert voice["schema_version"] == 2
    interface = json.loads((source / "interface-preferences.json").read_text(encoding="utf-8"))
    assert interface["setup_complete"] is False
    assert interface["send_enabled"] is True
    messages = json.loads((source / "message-profile.json").read_text(encoding="utf-8"))
    assert len(messages["messages"]) == 1


def test_factory_calibration_seeds_empty_profile_and_preserves_user_data(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_WHIP_DATA_DIR", str(tmp_path))
    first = import_factory_calibration_once()
    assert set(first.imported) == FACTORY_DEFAULT_FILES
    assert not first.errors

    custom = tmp_path / "mounting-profile.json"
    custom.write_text('{"custom": true}', encoding="utf-8")
    second = import_factory_calibration_once()
    assert "mounting-profile.json" in second.preserved
    assert custom.read_text(encoding="utf-8") == '{"custom": true}'


def test_restore_factory_defaults_backs_up_personal_settings_and_preserves_device_data(
    tmp_path: Path,
) -> None:
    target = tmp_path / "user"
    target.mkdir()
    (target / "interface-preferences.json").write_text(
        '{"setup_complete": true, "send_enabled": false}', encoding="utf-8"
    )
    (target / "sensor-bias-profiles.json").write_text(
        '{"devices": {"one-device": {}}}', encoding="utf-8"
    )
    (target / "last-voice-recording.wav").write_bytes(b"personal audio")
    result = restore_factory_defaults(target, setup_complete=True)
    assert not result.errors
    assert set(result.restored) == FACTORY_DEFAULT_FILES
    restored = json.loads((target / "interface-preferences.json").read_text(encoding="utf-8"))
    assert restored["setup_complete"] is True
    assert restored["send_enabled"] is True
    assert (target / "sensor-bias-profiles.json").read_text(encoding="utf-8") == (
        '{"devices": {"one-device": {}}}'
    )
    assert (target / "last-voice-recording.wav").read_bytes() == b"personal audio"
    assert result.backup is not None
    backup = json.loads(
        (result.backup / "interface-preferences.json").read_text(encoding="utf-8")
    )
    assert backup["send_enabled"] is False


def test_restore_factory_defaults_rejects_tampered_bundle(tmp_path: Path) -> None:
    source = bundled_factory_calibration_dir()
    assert source is not None
    fake = tmp_path / "bundle"
    fake.mkdir()
    for path in source.iterdir():
        (fake / path.name).write_bytes(path.read_bytes())
    (fake / "voice-settings.json").write_text('{}', encoding="utf-8")
    target = tmp_path / "user"
    result = restore_factory_defaults(target, setup_complete=False, source=fake)
    assert result.errors
    assert not result.restored
    assert not target.exists()
