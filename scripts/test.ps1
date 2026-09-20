[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw 'Run scripts\setup-desktop.ps1 first.'
}

& $pythonPath -m pytest (Join-Path $projectRoot 'desktop\tests')
& $pythonPath -m codex_whip.cli simulate

