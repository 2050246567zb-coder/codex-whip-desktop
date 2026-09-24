"""Append-only, local diagnostics for completed speech recognition sessions.

The file deliberately contains no API credentials or audio bytes.  A saved
transcript can be private, so callers should share the file only deliberately.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path


class RecognitionHistory:
    def __init__(self, settings_path: Path) -> None:
        self.path = settings_path.with_name("recognition-history.jsonl")
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as output:
                output.write(line + "\n")
