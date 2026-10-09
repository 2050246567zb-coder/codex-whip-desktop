"""One macOS GUI per user profile, including copies launched from a DMG."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TextIO


class AppInstance:
    def __init__(self, directory: Path):
        self.directory = directory
        self.file: TextIO | None = None
        self.existing_pid: int | None = None

    def acquire(self) -> bool:
        import fcntl

        self.directory.mkdir(parents=True, exist_ok=True)
        handle = (self.directory / "app-instance.lock").open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.seek(0)
            try:
                self.existing_pid = int(json.load(handle)["pid"])
            except (ValueError, KeyError, TypeError):
                pass
            handle.close()
            return False
        handle.seek(0)
        handle.truncate()
        json.dump({"pid": os.getpid()}, handle)
        handle.flush()
        self.file = handle
        return True

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        # Keep the inode: deleting a locked file allows another copy to create
        # an unrelated lock at the same path. Process exit releases flock.
