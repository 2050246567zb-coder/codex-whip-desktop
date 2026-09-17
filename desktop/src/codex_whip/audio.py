from __future__ import annotations

import os
import sys


def system_beep(kind: str = "default") -> bool:
    if os.name == "nt":
        import winsound

        values = {
            "start": winsound.MB_ICONASTERISK,
            "ok": winsound.MB_OK,
            "error": winsound.MB_ICONHAND,
        }
        winsound.MessageBeep(values.get(kind, winsound.MB_OK))
        return True
    if sys.platform == "darwin":
        from .macos_api import beep

        return beep(kind)
    return False
