from __future__ import annotations

import hashlib
import json
from pathlib import Path

from codex_whip import paths
from codex_whip.migration import import_migration_data


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
