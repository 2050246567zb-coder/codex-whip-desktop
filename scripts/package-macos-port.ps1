param(
    [string]$Destination = ""
)

$ErrorActionPreference = "Stop"
$root = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$version = "2.0.1"

function Get-PackageRelativePath {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $baseFull = [System.IO.Path]::GetFullPath($BasePath).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    if (-not $pathFull.StartsWith($baseFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Package source escaped its root: $pathFull"
    }
    return $pathFull.Substring($baseFull.Length)
}

$outputRoot = Join-Path $root "dist"
if (-not $Destination) {
    $Destination = Join-Path $outputRoot "CodexWhip-macOS-$version-source-with-data.zip"
}
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($destinationPath)) | Out-Null

$required = @(
    "desktop\pyproject.toml",
    "firmware\codex_whip\codex_whip.ino",
    "macos\build-macos.sh",
    "macos\migration-data\migration-manifest.json"
)
foreach ($relative in $required) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $relative) -PathType Leaf)) {
        throw "Missing required package file: $relative"
    }
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = "python"
}
& $python (Join-Path $root "scripts\package_macos_data.py") --verify (Join-Path $root "macos\migration-data")
if ($LASTEXITCODE -ne 0) {
    throw "Migration data verification failed"
}

$stage = Join-Path ([System.IO.Path]::GetTempPath()) ("codex-whip-macos-" + [guid]::NewGuid().ToString("N"))
$stageRoot = Join-Path $stage "CodexWhip-macOS-$version"
New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null
try {
    foreach ($name in @("desktop", "firmware", "macos", "scripts")) {
        $source = Join-Path $root $name
        Get-ChildItem -LiteralPath $source -File -Recurse -Force | Where-Object {
            $relative = Get-PackageRelativePath -BasePath $source -Path $_.FullName
            $parts = $relative -split '[\\/]'
            -not ($parts | Where-Object {
                $_ -in @("__pycache__", ".pytest_cache", ".venv-macos", ".build", "build", "dist")
            }) -and $_.Extension -notin @(".pyc", ".pyo")
        } | ForEach-Object {
            $relative = Get-PackageRelativePath -BasePath $source -Path $_.FullName
            $target = Join-Path (Join-Path $stageRoot $name) $relative
            New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetDirectoryName($target)) | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $target -Force
        }
    }
    Copy-Item -LiteralPath (Join-Path $root "README.md") -Destination $stageRoot
    if (Test-Path -LiteralPath $destinationPath) {
        Remove-Item -LiteralPath $destinationPath -Force
    }
    Compress-Archive -LiteralPath $stageRoot -DestinationPath $destinationPath -CompressionLevel Optimal

    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [System.IO.Compression.ZipFile]::OpenRead($destinationPath)
    try {
        $entryNames = [System.Collections.Generic.HashSet[string]]::new(
            [string[]]($archive.Entries | ForEach-Object FullName),
            [System.StringComparer]::Ordinal
        )
        $packageRoot = "CodexWhip-macOS-$version/"
        foreach ($relative in @(
            "desktop/pyproject.toml",
            "firmware/codex_whip/codex_whip.ino",
            "macos/build-macos.sh",
            "macos/migration-data/migration-manifest.json",
            "macos/migration-data/double-tap-profile-v2.json",
            "macos/migration-data/voice/ggml-small-q5_1.bin"
        )) {
            if (-not $entryNames.Contains($packageRoot + $relative)) {
                throw "Archive verification failed; missing $relative"
            }
        }
    }
    finally {
        $archive.Dispose()
    }
}
finally {
    $resolvedStage = [System.IO.Path]::GetFullPath($stage)
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedStage.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        $resolvedStage -ne $tempRoot -and
        (Test-Path -LiteralPath $resolvedStage)) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}

$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $destinationPath).Hash
Write-Output "PACKAGE=$destinationPath"
Write-Output "SHA256=$hash"
