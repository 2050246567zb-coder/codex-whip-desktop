[CmdletBinding()]
param([ValidateSet('codex_whip_esp32c3','c3_usb_imu_diagnostic')][string]$SketchName='codex_whip_esp32c3',
      [ValidatePattern('^[P-Z]:$')][string]$AsciiDataDrive='P:')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$cli = Join-Path $root '.tools/arduino-cli-1.5.1/arduino-cli.exe'
if (!(Test-Path -LiteralPath $cli)) {
    $cli = (Get-Command arduino-cli -ErrorAction Stop).Source
}
# Uses the normal Arduino data directory, not the project's nRF52-only config.
# Espressif's Windows linker cannot write ELF outputs under Unicode paths.
$cache = 'C:/Users/Public/CodexWhipBuild/c3-070'
if ($SketchName -ne 'codex_whip_esp32c3') { $cache = 'C:/Users/Public/CodexWhipBuild/c3-usb-diag' }
$outputName = if ($SketchName -eq 'codex_whip_esp32c3') { 'firmware-esp32c3' } else { 'firmware-c3-usb-diag' }
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$data = Join-Path $env:LOCALAPPDATA 'Arduino15'
$mapped = $false
$previousData = $env:ARDUINO_DIRECTORIES_DATA
try {
    if (Test-Path "$AsciiDataDrive/") {
        $mapping = (& subst) -join "`n"
        if (!$mapping.Contains("$AsciiDataDrive\: => $data")) { throw "$AsciiDataDrive is occupied or cannot be verified; do not overwrite it." }
    } else {
        & subst $AsciiDataDrive $data
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create temporary ASCII SDK mapping.' }
        $mapped = $true
    }
    $env:ARDUINO_DIRECTORIES_DATA = "$AsciiDataDrive/"
    & $cli compile --jobs 4 --build-path $cache --fqbn 'esp32:esp32:esp32c3:CDCOnBoot=cdc,PartitionScheme=huge_app' --output-dir (Join-Path $root "build/$outputName") (Join-Path $root "firmware/$SketchName")
    if ($LASTEXITCODE -ne 0) { throw 'ESP32-C3 firmware compilation failed.' }
} finally {
    $env:ARDUINO_DIRECTORIES_DATA = $previousData
    if ($mapped) { & subst $AsciiDataDrive /d }
}
