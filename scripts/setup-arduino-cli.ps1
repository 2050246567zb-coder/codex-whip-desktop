[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$toolRoot = Join-Path $projectRoot '.tools\arduino-cli-1.5.1'
$cliPath = Join-Path $toolRoot 'arduino-cli.exe'
$downloadRoot = Join-Path $projectRoot '.tools\downloads'
$archivePath = Join-Path $downloadRoot 'arduino-cli_1.5.1_Windows_64bit.zip'
$checksumsPath = Join-Path $downloadRoot '1.5.1-checksums.txt'
$releaseRoot = 'https://github.com/arduino/arduino-cli/releases/download/v1.5.1'
$configPath = Join-Path $projectRoot '.arduino-cli.yaml'

New-Item -ItemType Directory -Force -Path $toolRoot, $downloadRoot | Out-Null

if (-not (Test-Path -LiteralPath $cliPath)) {
    Invoke-WebRequest -UseBasicParsing -Uri "$releaseRoot/arduino-cli_1.5.1_Windows_64bit.zip" -OutFile $archivePath
    Invoke-WebRequest -UseBasicParsing -Uri "$releaseRoot/1.5.1-checksums.txt" -OutFile $checksumsPath

    $checksumLine = Select-String -LiteralPath $checksumsPath -Pattern 'arduino-cli_1.5.1_Windows_64bit.zip' | Select-Object -First 1
    if (-not $checksumLine) {
        throw 'The official checksum file does not list the Windows 64-bit archive.'
    }
    $expectedHash = ($checksumLine.Line -split '\s+')[0].ToUpperInvariant()
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archivePath).Hash.ToUpperInvariant()
    if ($actualHash -ne $expectedHash) {
        throw "Arduino CLI checksum mismatch. Expected $expectedHash, got $actualHash."
    }
    Expand-Archive -LiteralPath $archivePath -DestinationPath $toolRoot -Force
}

& $cliPath config init --overwrite --dest-file $configPath
& $cliPath config set --config-file $configPath directories.data (Join-Path $projectRoot '.arduino-data')
& $cliPath config set --config-file $configPath directories.downloads (Join-Path $projectRoot '.arduino-data\staging')
& $cliPath config set --config-file $configPath directories.user (Join-Path $projectRoot '.arduino-user')
& $cliPath config set --config-file $configPath board_manager.additional_urls 'https://files.seeedstudio.com/arduino/package_seeeduino_boards_index.json'
& $cliPath core update-index --config-file $configPath
& $cliPath core install 'Seeeduino:nrf52@1.1.13' --config-file $configPath
& $cliPath lib update-index --config-file $configPath
& $cliPath lib install 'Seeed Arduino LSM6DS3@2.0.5' --config-file $configPath

Write-Host "Arduino CLI is ready: $cliPath"

