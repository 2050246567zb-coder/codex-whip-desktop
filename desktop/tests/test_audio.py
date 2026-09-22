import sys
import threading
import time
from types import SimpleNamespace

from codex_whip import audio


def test_windows_system_beep_never_blocks_caller(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def blocked_message_beep(_value):
        started.set()
        release.wait(1)

    fake_winsound = SimpleNamespace(
        MB_ICONASTERISK=1,
        MB_OK=2,
        MB_ICONHAND=3,
        MessageBeep=blocked_message_beep,
    )
    monkeypatch.setattr(audio.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)

    before = time.perf_counter()
    assert audio.system_beep("start")
    elapsed = time.perf_counter() - before

    assert elapsed < 0.2
    assert started.wait(0.5)
    release.set()


def test_macos_beep_stays_on_ui_thread(monkeypatch):
    seen = []
    monkeypatch.setattr(audio.os, "name", "posix")
    monkeypatch.setattr(audio.sys, "platform", "darwin")
    monkeypatch.setattr(audio, "_play_system_beep", lambda kind: seen.append((kind, threading.get_ident())))
    assert audio.system_beep("start")
    assert seen == [("start", threading.get_ident())]
