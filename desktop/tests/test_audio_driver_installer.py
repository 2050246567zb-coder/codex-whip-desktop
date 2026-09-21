from io import BytesIO
import hashlib
from pathlib import Path
import subprocess
import zipfile

import pytest

from codex_whip import audio_driver_installer as module
from codex_whip.audio_driver_installer import (
    AudioDriverInstaller,
    AudioDriverInstallError,
    MACOS_INSTALL_PAGE,
)


class Download(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def windows_bundle() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for name in module._WINDOWS_FILES:
            bundle.writestr(name, b"signed fixture")
    return output.getvalue()


def test_windows_download_is_pinned_verified_and_elevated(tmp_path, monkeypatch):
    payload = windows_bundle()
    monkeypatch.setattr(module, "WINDOWS_RELEASE_SHA256", hashlib.sha256(payload).hexdigest())
    elevated = []
    commands = []

    def run(args, **_kwargs):
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, b"VALID\r\n", b"")

    installer = AudioDriverInstaller(
        platform="win32",
        downloader=lambda url, timeout: Download(payload),
        runner=run,
        elevate=lambda inf: elevated.append(inf) or True,
        temp_root=tmp_path,
    )
    result = installer.install()
    assert result.automatic
    assert elevated[0].name == "VirtualAudioDriver.inf"
    assert elevated[0].exists()
    assert commands[0][0] == "powershell.exe"
    assert str(elevated[0].parent / "virtualaudiodriver.cat") in commands[0][-1]
    assert installer._working_directories


def test_windows_hash_mismatch_never_elevates(tmp_path):
    elevated = []
    installer = AudioDriverInstaller(
        platform="win32",
        downloader=lambda url, timeout: Download(windows_bundle()),
        elevate=lambda inf: elevated.append(inf) or True,
        temp_root=tmp_path,
    )
    with pytest.raises(AudioDriverInstallError, match="校验失败"):
        installer.install()
    assert not elevated


def test_macos_uses_visible_terminal_homebrew_install(tmp_path):
    brew = tmp_path / "brew"
    brew.write_text("")
    calls = []

    def run(args, **_kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    result = AudioDriverInstaller(platform="darwin", which=lambda _name: str(brew), runner=run).install()
    assert result.automatic
    assert calls[0][0] == "/usr/bin/osascript"
    assert any("install --cask blackhole-2ch" in value for value in calls[0])


def test_macos_without_homebrew_opens_official_installation_page():
    opened = []
    result = AudioDriverInstaller(
        platform="darwin", which=lambda _name: None, brew_paths=(),
        opener=lambda url: opened.append(url) or True
    ).install()
    assert not result.automatic
    assert opened == [MACOS_INSTALL_PAGE]
