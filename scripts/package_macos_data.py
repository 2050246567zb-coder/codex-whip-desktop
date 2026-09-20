from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


PROFILE_FILES = (
    "config.toml",
    "detector-profile.json",
    "double-tap-profile-v2.json",
    "message-profile.json",
    "motion-profile-v3.json",
    "overlay-position.json",
    "visual-settings.json",
    "voice-settings.json",
)


def verify_package(output: Path) -> tuple[int, int]:
    manifest_path = output / "migration-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files")
    if manifest.get("schema_version") != 1 or not isinstance(entries, list):
        raise ValueError("invalid migration manifest")
    expected: set[str] = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("invalid migration manifest entry")
        relative = str(entry.get("path", "")).replace("\\", "/").strip("/")
        parts = Path(relative).parts
        if not relative or ".." in parts or relative in expected:
            raise ValueError(f"unsafe or duplicate migration path: {relative!r}")
        expected.add(relative)
        path = output / Path(relative)
        size = int(entry["size"])
        sha256 = str(entry["sha256"]).lower()
        if not path.is_file() or path.stat().st_size != size or digest(path) != sha256:
            raise ValueError(f"migration file failed verification: {relative}")
        total += size
    actual = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "migration-manifest.json"
    }
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"manifest/file mismatch; missing={missing}, extra={extra}")
    return len(expected), total


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def copy_if_present(source: Path, target: Path) -> bool:
    if not source.is_file():
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify an existing package without changing it",
    )
    parser.add_argument(
        "--local-data",
        type=Path,
        default=Path(os.environ.get("LOCALAPPDATA", ".")) / "CodexWhip",
    )
    parser.add_argument(
        "--runtime-data",
        type=Path,
        default=Path(os.environ.get("PROGRAMDATA", ".")) / "CodexWhip",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if args.verify:
        count, total = verify_package(output)
        print(f"verified {count} files, {total} bytes -> {output}")
        return 0
    output.mkdir(parents=True, exist_ok=True)

    for filename in PROFILE_FILES:
        copy_if_present(args.local_data / filename, output / filename)
    local_voice = args.local_data / "voice"
    runtime_voice = args.runtime_data / "voice"
    for source_root in (local_voice, runtime_voice):
        if not source_root.is_dir():
            continue
        for source in source_root.iterdir():
            if source.is_file():
                copy_if_present(source, output / "voice" / source.name)

    files = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "migration-manifest.json":
            continue
        files.append(
            {
                "path": path.relative_to(output).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest(path),
            }
        )
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_platform": "windows",
        "files": files,
    }
    (output / "migration-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    total = sum(item["size"] for item in files)
    print(f"packaged {len(files)} files, {total} bytes -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
