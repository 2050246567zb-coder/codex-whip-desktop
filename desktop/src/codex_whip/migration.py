from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .paths import user_data_dir


MIGRATION_MANIFEST = "migration-manifest.json"
FACTORY_CALIBRATION_DIRECTORY = "factory-calibration"
FACTORY_DEFAULT_FILES = frozenset({
    "detector-profile.json", "double-tap-profile-v2.json",
    "interface-preferences.json", "message-profile.json",
    "mounting-profile.json", "power-settings.json",
    "visual-settings.json", "voice-settings.json",
    "whip-sensitivity.json",
})


@dataclass(frozen=True, slots=True)
class MigrationResult:
    source: Path | None
    imported: tuple[str, ...]
    preserved: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FactoryRestoreResult:
    restored: tuple[str, ...]
    backup: Path | None
    errors: tuple[str, ...]


def bundled_migration_dir() -> Path | None:
    override = os.environ.get("CODEX_WHIP_MIGRATION_DATA")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    if getattr(sys, "frozen", False):
        candidates.append(Path(getattr(sys, "_MEIPASS")) / "migration-data")
        candidates.append(Path(sys.executable).resolve().parent / "migration-data")
    candidates.append(Path(__file__).resolve().parents[3] / "macos" / "migration-data")
    return next(
        (
            candidate
            for candidate in candidates
            if (candidate / MIGRATION_MANIFEST).is_file()
        ),
        None,
    )


def bundled_factory_calibration_dir() -> Path | None:
    """Locate the public, sanitized factory calibration shipped with the app."""

    override = os.environ.get("CODEX_WHIP_FACTORY_CALIBRATION")
    candidates: list[Path] = []
    if override:
        candidates.append(Path(override).expanduser())
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS"))
        candidates.append(root / "assets" / FACTORY_CALIBRATION_DIRECTORY)
        candidates.append(
            Path(sys.executable).resolve().parent
            / "assets"
            / FACTORY_CALIBRATION_DIRECTORY
        )
    candidates.append(
        Path(__file__).resolve().parents[2]
        / "assets"
        / FACTORY_CALIBRATION_DIRECTORY
    )
    return next(
        (
            candidate
            for candidate in candidates
            if (candidate / MIGRATION_MANIFEST).is_file()
        ),
        None,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def import_migration_data(
    source: Path,
    destination: Path | None = None,
) -> MigrationResult:
    target_root = destination or user_data_dir()
    imported: list[str] = []
    preserved: list[str] = []
    errors: list[str] = []
    try:
        manifest = json.loads((source / MIGRATION_MANIFEST).read_text(encoding="utf-8"))
        files = manifest.get("files", [])
        if manifest.get("schema_version") != 1 or not isinstance(files, list):
            raise ValueError("迁移清单格式无效")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return MigrationResult(source, (), (), (str(exc),))

    for entry in files:
        if not isinstance(entry, dict):
            errors.append("迁移清单包含无效文件项")
            continue
        relative = str(entry.get("path", "")).replace("\\", "/").strip("/")
        if not relative or ".." in Path(relative).parts:
            errors.append(f"拒绝不安全的迁移路径：{relative!r}")
            continue
        source_file = source / Path(relative)
        target_file = target_root / Path(relative)
        try:
            expected_size = int(entry["size"])
            expected_hash = str(entry["sha256"]).lower()
            if (
                not source_file.is_file()
                or source_file.stat().st_size != expected_size
                or _sha256(source_file).lower() != expected_hash
            ):
                raise OSError(f"源文件校验失败：{relative}")
            if target_file.exists():
                preserved.append(relative)
                continue
            target_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = target_file.with_suffix(target_file.suffix + ".importing")
            shutil.copyfile(source_file, temporary)
            if temporary.stat().st_size != expected_size or _sha256(temporary) != expected_hash:
                temporary.unlink(missing_ok=True)
                raise OSError(f"复制后校验失败：{relative}")
            temporary.replace(target_file)
            imported.append(relative)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            errors.append(str(exc))

    return MigrationResult(
        source,
        tuple(imported),
        tuple(preserved),
        tuple(errors),
    )


def import_bundled_profile_once() -> MigrationResult:
    source = bundled_migration_dir()
    if source is None:
        return MigrationResult(None, (), (), ())
    return import_migration_data(source)


def import_factory_calibration_once() -> MigrationResult:
    """Seed approved defaults without replacing any existing user choices."""

    source = bundled_factory_calibration_dir()
    if source is None:
        return MigrationResult(None, (), (), ())
    return import_migration_data(source)


def restore_factory_defaults(
    destination: Path | None = None,
    *,
    setup_complete: bool,
    source: Path | None = None,
) -> FactoryRestoreResult:
    """Restore verified defaults, preserving onboarding and device-local data.

    Existing settings are backed up before replacement. A failure during the
    replacement phase rolls back changed files from their in-memory originals.
    """
    source = source or bundled_factory_calibration_dir()
    if source is None:
        return FactoryRestoreResult((), None, ("安装包中没有出厂预设",))
    target_root = destination or user_data_dir()
    try:
        manifest = json.loads((source / MIGRATION_MANIFEST).read_text(encoding="utf-8"))
        entries = manifest["files"]
        if manifest.get("schema_version") != 1 or not isinstance(entries, list):
            raise ValueError("出厂预设清单格式无效")
        if {entry["path"] for entry in entries} != FACTORY_DEFAULT_FILES:
            raise ValueError("出厂预设文件不完整或包含未批准的数据")
        payloads: dict[str, bytes] = {}
        for entry in entries:
            name = entry["path"]
            data = (source / name).read_bytes()
            if (len(data) != int(entry["size"])
                    or hashlib.sha256(data).hexdigest() != entry["sha256"]):
                raise ValueError(f"出厂预设校验失败：{name}")
            payloads[name] = data
        interface = json.loads(payloads["interface-preferences.json"])
        interface["setup_complete"] = bool(setup_complete)
        payloads["interface-preferences.json"] = (
            json.dumps(interface, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        return FactoryRestoreResult((), None, (str(exc),))

    backup = (target_root / "factory-backups"
              / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}")
    originals: dict[str, bytes | None] = {}
    changed: list[str] = []
    try:
        backup.mkdir(parents=True, exist_ok=False)
        for name in sorted(payloads):
            path = target_root / name
            old = path.read_bytes() if path.exists() else None
            originals[name] = old
            if old is not None:
                (backup / name).write_bytes(old)
        for name, data in sorted(payloads.items()):
            path = target_root / name
            temporary = path.with_suffix(path.suffix + ".restoring")
            temporary.write_bytes(data)
            temporary.replace(path)
            changed.append(name)
    except OSError as exc:
        for name in reversed(changed):
            path = target_root / name
            previous = originals[name]
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    rollback = path.with_suffix(path.suffix + ".rollback")
                    rollback.write_bytes(previous)
                    rollback.replace(path)
            except OSError:
                pass  # The backup remains available for manual recovery.
        return FactoryRestoreResult((), backup if backup.exists() else None,
                                    (str(exc),))
    return FactoryRestoreResult(tuple(changed), backup, ())
