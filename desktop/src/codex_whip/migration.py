from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .paths import user_data_dir


MIGRATION_MANIFEST = "migration-manifest.json"


@dataclass(frozen=True, slots=True)
class MigrationResult:
    source: Path | None
    imported: tuple[str, ...]
    preserved: tuple[str, ...]
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
