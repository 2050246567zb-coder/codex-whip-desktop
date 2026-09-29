"""Put the verified offline Whisper model in the Mac app's bundled assets."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import urllib.request

from codex_whip.voice import (
    MODEL_NAME, MODEL_SHA256, MODEL_SIZE, MODEL_URL, WHISPER_VERSION,
)


def matches(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size != MODEL_SIZE:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest() == MODEL_SHA256


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "desktop" / "assets" / "stt" / f"whispercpp-{WHISPER_VERSION}" / MODEL_NAME
    if matches(target):
        print("Verified bundled Whisper model:", target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".bin.preparing")
    temporary.unlink(missing_ok=True)
    source_override = os.environ.get("CODEX_WHIP_WHISPER_MODEL_SOURCE")
    try:
        if source_override:
            source = Path(source_override).expanduser()
            if not matches(source):
                raise SystemExit("Local Whisper model failed size/SHA-256 verification")
            shutil.copyfile(source, temporary)
        else:
            request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "CodexWhip-Build/1"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, 1024 * 1024)
        if not matches(temporary):
            raise SystemExit("Downloaded Whisper model failed size/SHA-256 verification")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    print("Verified bundled Whisper model:", target)


if __name__ == "__main__":
    main()
