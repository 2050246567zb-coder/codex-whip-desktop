[CmdletBinding()]
param(
    [string]$Name = 'CodexWhip'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$desktopRoot = Join-Path $projectRoot 'desktop'
$entryPoint = Join-Path $desktopRoot 'codex_whip_gui.py'
$distPath = Join-Path $projectRoot 'dist'
$workPath = Join-Path $projectRoot 'build\pyinstaller'
$specPath = Join-Path $projectRoot 'build\pyinstaller-spec'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run scripts\setup-desktop.ps1 first.'
}

if ($env:CODEX_WHIP_DOUBAO_API_KEY) {
    $privateAssetPath = Join-Path $desktopRoot 'assets\private'
    New-Item -ItemType Directory -Force -Path $privateAssetPath | Out-Null
    Set-Content -LiteralPath (Join-Path $privateAssetPath 'doubao-api-key.txt') `
        -Value $env:CODEX_WHIP_DOUBAO_API_KEY -NoNewline
}

& $pythonPath -m pip install --disable-pip-version-check -e "$desktopRoot[build]"
if ($LASTEXITCODE -ne 0) {
    throw "Installing build dependencies failed with exit code $LASTEXITCODE."
}

& $pythonPath -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $Name `
    --paths (Join-Path $desktopRoot 'src') `
    --add-data "$(Join-Path $desktopRoot 'assets');assets" `
    --collect-all bleak `
    --collect-all pywinauto `
    --collect-all sounddevice `
    --exclude-module pytest `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    $entryPoint

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

Copy-Item -LiteralPath (Join-Path $desktopRoot 'config.example.toml') `
    -Destination (Join-Path $distPath 'config.example.toml') -Force
Write-Host "Windows app built: $(Join-Path $distPath ($Name + '.exe'))"
