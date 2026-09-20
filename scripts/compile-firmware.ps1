[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cliPath = Join-Path $projectRoot '.tools\arduino-cli-1.5.1\arduino-cli.exe'
$configPath = Join-Path $projectRoot '.arduino-cli.yaml'
$sketchPath = Join-Path $projectRoot 'firmware\codex_whip'
$buildPath = Join-Path $projectRoot 'build\firmware'
$fqbn = 'Seeeduino:nrf52:xiaonRF52840Sense'

if (-not (Test-Path -LiteralPath $cliPath) -or -not (Test-Path -LiteralPath $configPath)) {
    throw 'Run scripts\setup-arduino-cli.ps1 first.'
}

& $cliPath compile --config-file $configPath --fqbn $fqbn --output-dir $buildPath $sketchPath
if ($LASTEXITCODE -ne 0) {
    throw "Firmware compilation failed with exit code $LASTEXITCODE."
}
