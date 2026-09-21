"""Launch verified, platform-native virtual audio driver installation."""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import hashlib
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
from typing import Callable
from urllib.request import urlopen
import webbrowser
import zipfile


WINDOWS_RELEASE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"
WINDOWS_RELEASE_SHA256 = "b950e39f01af1d04ea623c8f6d8eb9b6ea5c477c637295fabf20631c85116bfb"
WINDOWS_RELEASE_PAGE = "https://vb-audio.com/Cable/"
MACOS_INSTALL_PAGE = "https://github.com/ExistentialAudio/BlackHole/wiki/Installation"
_WINDOWS_REQUIRED_FILES = {
    "VBCABLE_Setup_x64.exe",
    "vbaudio_cable64_win10.cat",
    "vbaudio_cable64_win10.sys",
    "vbMmeCable64_win10.inf",
}


class AudioDriverInstallError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class InstallLaunchResult:
    message: str
    automatic: bool


class AudioDriverInstaller:
    """Download only a pinned signed driver, then hand control to the OS installer."""

    def __init__(
        self,
        *,
        platform: str | None = None,
        opener: Callable[[str], bool] = webbrowser.open,
        runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        which: Callable[[str], str | None] = shutil.which,
        downloader: Callable = urlopen,
        temp_root: Path | None = None,
        elevate: Callable[[Path], bool] | None = None,
        brew_paths: tuple[str, ...] = ("/opt/homebrew/bin/brew", "/usr/local/bin/brew"),
    ) -> None:
        self.platform = platform or sys.platform
        self._opener = opener
        self._runner = runner
        self._which = which
        self._downloader = downloader
        self._temp_root = temp_root
        self._elevate = elevate or self._windows_elevate
        self._brew_paths = brew_paths
        # Keep extracted INF/CAT/SYS alive until the elevated installer has read them.
        self._working_directories: list[Path] = []

    @property
    def product_name(self) -> str:
        return "BlackHole 2ch" if self.platform == "darwin" else "VB-CABLE"

    @property
    def source_url(self) -> str:
        return MACOS_INSTALL_PAGE if self.platform == "darwin" else WINDOWS_RELEASE_PAGE

    def install(self) -> InstallLaunchResult:
        if self.platform == "win32":
            return self._install_windows()
        if self.platform == "darwin":
            return self._install_macos()
        raise AudioDriverInstallError("当前系统暂不支持自动安装虚拟音频驱动")

    def _install_windows(self) -> InstallLaunchResult:
        work = Path(tempfile.mkdtemp(prefix="codex-whip-audio-", dir=self._temp_root))
        archive = work / "virtual-audio-driver.zip"
        try:
            digest = hashlib.sha256()
            total = 0
            with self._downloader(WINDOWS_RELEASE_URL, timeout=30) as response, archive.open("wb") as output:
                while chunk := response.read(64 * 1024):
                    total += len(chunk)
                    if total > 10 * 1024 * 1024:
                        raise AudioDriverInstallError("驱动下载大小异常，已停止安装")
                    digest.update(chunk)
                    output.write(chunk)
            if digest.hexdigest().casefold() != WINDOWS_RELEASE_SHA256:
                raise AudioDriverInstallError("驱动校验失败，未启动安装")
            with zipfile.ZipFile(archive) as bundle:
                files = {item.filename for item in bundle.infolist() if not item.is_dir()}
                if not _WINDOWS_REQUIRED_FILES.issubset(files):
                    raise AudioDriverInstallError("驱动包内容与已验证版本不一致")
                for item in bundle.infolist():
                    target = (work / item.filename).resolve()
                    if work.resolve() not in target.parents and target != work.resolve():
                        raise AudioDriverInstallError("驱动包包含不安全路径")
                bundle.extractall(work)
            setup = work / "VBCABLE_Setup_x64.exe"
            self._verify_windows_signature(setup, "BUREL VINCENT")
            self._verify_windows_signature(
                work / "vbaudio_cable64_win10.cat", "Microsoft Windows Hardware Compatibility Publisher")
            if not self._elevate(setup):
                raise AudioDriverInstallError("系统没有启动驱动安装；请允许管理员确认后重试")
            self._working_directories.append(work)
            return InstallLaunchResult(
                "VB-CABLE 安装器已打开；点击 Install Driver，完成后重启电脑并重新检测", True)
        except AudioDriverInstallError:
            shutil.rmtree(work, ignore_errors=True)
            raise
        except (OSError, TimeoutError, zipfile.BadZipFile) as exc:
            shutil.rmtree(work, ignore_errors=True)
            raise AudioDriverInstallError(f"无法准备音频驱动：{exc}") from exc

    def _verify_windows_signature(self, file: Path, signer: str) -> None:
        literal_path = str(file).replace("'", "''")
        signer_fragment = signer.replace("'", "''")
        script = (
            "Import-Module (Join-Path $PSHOME "
            "'Modules\\Microsoft.PowerShell.Security\\Microsoft.PowerShell.Security.psd1');"
            f"$s=Get-AuthenticodeSignature -LiteralPath '{literal_path}';"
            "if($s.Status -ne 'Valid'){exit 2};"
            f"if($s.SignerCertificate.Subject -notlike '*{signer_fragment}*'){{exit 3}};"
            "Write-Output 'VALID'"
        )
        try:
            result = self._runner(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=20,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AudioDriverInstallError(f"无法验证 Windows 驱动签名：{exc}") from exc
        if result.returncode != 0 or b"VALID" not in result.stdout:
            raise AudioDriverInstallError("Windows 驱动数字签名无效，未启动安装")

    @staticmethod
    def _windows_elevate(installer: Path) -> bool:
        if sys.platform != "win32":
            return False
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(installer), None, str(installer.parent), 1
        )
        return int(result) > 32

    def _install_macos(self) -> InstallLaunchResult:
        brew = next(
            (path for path in (*self._brew_paths, self._which("brew"))
             if path and Path(path).exists()),
            None,
        )
        if not brew:
            if not self._opener(MACOS_INSTALL_PAGE):
                raise AudioDriverInstallError("无法打开 BlackHole 官方安装页面")
            return InstallLaunchResult("未检测到 Homebrew，已打开 BlackHole 官方安装页面", False)
        command = f"{shlex.quote(str(brew))} install --cask blackhole-2ch"
        apple_script = [
            "tell application \"Terminal\"",
            "activate",
            f"do script {self._apple_script_string(command)}",
            "end tell",
        ]
        args = ["/usr/bin/osascript"]
        for line in apple_script:
            args.extend(("-e", line))
        try:
            result = self._runner(args, capture_output=True, text=True, timeout=20, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AudioDriverInstallError(f"无法打开 BlackHole 安装终端：{exc}") from exc
        if result.returncode != 0:
            raise AudioDriverInstallError(result.stderr.strip() or "无法启动 BlackHole 安装")
        return InstallLaunchResult("安装命令已在终端打开；完成后可能需要重启音频应用或 Mac", True)

    @staticmethod
    def _apple_script_string(value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
