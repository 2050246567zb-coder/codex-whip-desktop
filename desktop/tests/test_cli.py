from __future__ import annotations

import json
import sys
from types import SimpleNamespace

from codex_whip import cli
from codex_whip.settings import Settings


class _ReadySender:
    def diagnose(self) -> list[dict[str, object]]:
        return [{"title": "Codex", "pid": 42}]

    def check_ready(self) -> dict[str, object]:
        return {"title": "Codex", "pid": 42, "composer_empty": True}


def _install_fake_bleak(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "bleak", SimpleNamespace(__version__="test"))


def test_strict_doctor_passes_only_for_ready_composer(monkeypatch, capsys) -> None:
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli, "create_live_sender", lambda *_args, **_kwargs: _ReadySender())

    assert cli._doctor(Settings(), strict=True) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["codex_ready"]["composer_empty"] is True
    assert "failures" not in report


def test_strict_doctor_fails_when_window_count_is_ambiguous(monkeypatch, capsys) -> None:
    _install_fake_bleak(monkeypatch)
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    sender = _ReadySender()
    monkeypatch.setattr(sender, "diagnose", lambda: [{"pid": 1}, {"pid": 2}])
    monkeypatch.setattr(cli, "create_live_sender", lambda *_args, **_kwargs: sender)

    assert cli._doctor(Settings(), strict=True) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["failures"] == [
        "expected exactly one visible Codex window, found 2"
    ]
