"""Prepare and verify the offline model included in Windows product builds."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "desktop" / "src"))

from codex_whip.voice import (  # noqa: E402
    MODEL_NAME, MODEL_SHA256, MODEL_SIZE, MODEL_URL, WHISPER_VERSION,
    VAD_MODEL_NAME, VAD_MODEL_SHA256, VAD_MODEL_SIZE,
    _file_matches, default_voice_runtime_dir,
)


def verify_package(path: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(path))
    for name, size, digest in (
        (MODEL_NAME, MODEL_SIZE, MODEL_SHA256),
        (VAD_MODEL_NAME, VAD_MODEL_SIZE, VAD_MODEL_SHA256),
    ):
        entry = f"assets/stt/whispercpp-{WHISPER_VERSION}/{name}"
        matches = [key for key in archive.toc if key.replace("\\", "/") == entry]
        if len(matches) != 1:
            raise SystemExit(f"Packaged model missing or ambiguous: {name}")
        data = archive.extract(matches[0])
        if len(data) != size or hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f"Packaged model failed size/SHA-256 verification: {name}")
        print(f"Verified packaged offline model: {name} ({size} bytes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-package", type=Path)
    args = parser.parse_args()
    if args.verify_package:
        verify_package(args.verify_package)
        return

    target = ROOT / "desktop" / "assets" / "stt" / f"whispercpp-{WHISPER_VERSION}" / MODEL_NAME
    if _file_matches(target, MODEL_SIZE, MODEL_SHA256):
        print(f"Verified bundled offline model: {MODEL_NAME} ({MODEL_SIZE} bytes)")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".bin.preparing")
    override = os.environ.get("CODEX_WHIP_WHISPER_MODEL_SOURCE")
    cached = default_voice_runtime_dir() / MODEL_NAME
    try:
        if override:
            source = Path(override).expanduser()
            if not _file_matches(source, MODEL_SIZE, MODEL_SHA256):
                raise SystemExit("Provided offline model failed size/SHA-256 verification")
            shutil.copyfile(source, temporary)
        elif _file_matches(cached, MODEL_SIZE, MODEL_SHA256):
            shutil.copyfile(cached, temporary)
        else:
            request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "CodexWhip-Build/1"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
        if not _file_matches(temporary, MODEL_SIZE, MODEL_SHA256):
            raise SystemExit("Offline model failed size/SHA-256 verification")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Prepared bundled offline model: {MODEL_NAME} ({MODEL_SIZE} bytes)")


if __name__ == "__main__":
    main()
