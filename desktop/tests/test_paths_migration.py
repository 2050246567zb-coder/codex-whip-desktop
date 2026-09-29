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


def test_factory_calibration_is_sanitized_and_verified() -> None:
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
    assert "/Users/" not in combined
    assert "\\Users\\" not in combined
    assert "ble-device-preference" not in combined
    assert "setup_complete" not in combined
    assert "recognition-history" not in combined
    assert "sensor-bias" not in combined
    assert "last-voice-recording" not in combined
    assert "E2:30:F0:9F:D1:21" not in combined
    voice = json.loads((source / "voice-settings.json").read_text(encoding="utf-8"))
    assert voice["enabled"] is True
    assert voice["schema_version"] == 2
    assert voice["tap_force_calibrated"] is False
    assert voice["impact_dynamic_accel_g"] == 1.0
    assert voice["silence_ms"] == 2200
    assert json.loads((source / "power-settings.json").read_text())["enabled"] is True
    assert json.loads((source / "visual-settings.json").read_text())["strikes_per_wound"] == 2
    assert json.loads((source / "visual-settings.json").read_text())["sound_enabled"] is True
    assert json.loads((source / "message-profile.json").read_text())["messages"] == [
        "继续当前任务，先完成一个可验证的关键节点。"
    ]


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
    assert set(second.preserved) == FACTORY_DEFAULT_FILES
    assert not second.imported
    assert custom.read_text(encoding="utf-8") == '{"custom": true}'


def test_explicit_restore_replaces_only_allowlisted_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_WHIP_DATA_DIR", str(tmp_path))
    first = import_factory_calibration_once()
    assert set(first.imported) == FACTORY_DEFAULT_FILES
    (tmp_path / "voice-settings.json").write_text('{"personal": true}')
    untouched = {
        "ble-device-preference.json": '{"device": "private"}',
        "interface-preferences.json": '{"setup_complete": true, "send_enabled": false}',
        "overlay-position.json": '{"x": 12}',
        "recognition-history.jsonl": '{"transcript": "private"}\n',
        "sensor-bias.json": '{"bias": "private"}',
    }
    for name, value in untouched.items():
        (tmp_path / name).write_text(value)
    result = restore_factory_defaults()
    assert set(result.restored) == FACTORY_DEFAULT_FILES
    assert not result.errors
    assert result.backup is not None
    assert (result.backup / "voice-settings.json").read_text() == '{"personal": true}'
    assert json.loads((tmp_path / "voice-settings.json").read_text())["silence_ms"] == 2200
    for name, value in untouched.items():
        assert (tmp_path / name).read_text() == value


def test_restore_rejects_tampered_bundle_before_changing_user_data(tmp_path: Path) -> None:
    source = bundled_factory_calibration_dir()
    assert source is not None
    altered = tmp_path / "altered-bundle"
    altered.mkdir()
    for name in FACTORY_DEFAULT_FILES | {"migration-manifest.json"}:
        (altered / name).write_bytes((source / name).read_bytes())
    (altered / "voice-settings.json").write_text('{}')
    user = tmp_path / "user"
    user.mkdir()
    (user / "voice-settings.json").write_text('{"personal": true}')

    result = restore_factory_defaults(user, source=altered)

    assert result.errors
    assert not result.restored
    assert (user / "voice-settings.json").read_text() == '{"personal": true}'
    assert not (user / "factory-backups").exists()
