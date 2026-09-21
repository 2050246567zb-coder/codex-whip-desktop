from __future__ import annotations

import os
import sys
import threading


def _play_system_beep(kind: str) -> None:
    """Play a notification without ever owning the Tk/UI thread."""
    try:
        if os.name == "nt":
            import winsound

            values = {
                "start": winsound.MB_ICONASTERISK,
                "ok": winsound.MB_OK,
                "error": winsound.MB_ICONHAND,
            }
            winsound.MessageBeep(values.get(kind, winsound.MB_OK))
        elif sys.platform == "darwin":
            from .macos_api import beep

            beep(kind)
    except Exception:
        # A broken or unavailable output device must not affect recording.
        return


def system_beep(kind: str = "default") -> bool:
    if os.name != "nt" and sys.platform != "darwin":
        return False
    threading.Thread(
        target=_play_system_beep,
        args=(kind,),
        name="codex-whip-system-beep",
        daemon=True,
    ).start()
    return True
