[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^COM\d+$')]
    [string]$Port
)

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
if (-not (Test-Path -LiteralPath (Join-Path $buildPath 'codex_whip.ino.zip'))) {
    throw 'Run scripts\compile-firmware.ps1 before uploading.'
}

$uploadOutput = @(
    & $cliPath upload --config-file $configPath --fqbn $fqbn --port $Port --input-dir $buildPath $sketchPath 2>&1
)
$uploadExit = $LASTEXITCODE
$uploadOutput | ForEach-Object { Write-Host $_ }
$uploadText = $uploadOutput -join "`n"
if ($uploadExit -ne 0 -or $uploadText -notmatch 'Device programmed\.') {
    throw (
        "Firmware upload did not report 'Device programmed.' (exit code $uploadExit). " +
        "Reconnect the board or enter bootloader mode, close any Serial Monitor using $Port, and retry."
    )
}
