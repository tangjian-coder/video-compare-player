# Build vcplayer into dist\.
# Default: portable onefile exe. Use -OneDir for the folder build.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 [-OneDir]

param(
    [switch]$OneDir
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "venv python not found: $python"
}
$libmpv = Join-Path $root "vendor\libmpv-2.dll"
if (-not (Test-Path -LiteralPath $libmpv)) {
    throw "libmpv not found: $libmpv"
}
$assets = Join-Path $root "assets"
if (-not (Test-Path -LiteralPath $assets)) {
    throw "assets directory not found: $assets"
}
foreach ($required in @(
    (Join-Path $root "assets\icon.ico"),
    (Join-Path $root "scripts\version_info.txt"),
    (Join-Path $root "vcplayer.spec"),
    (Join-Path $root "vcplayer_onedir.spec")
)) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "build input not found: $required"
    }
}
& $python -c "import imageio_ffmpeg"
if ($LASTEXITCODE -ne 0) { throw "imageio-ffmpeg is not installed" }

& $python -m pip install --quiet "pyinstaller>=6.10"
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }

$buildDir = Join-Path $root "build"
$distDir = Join-Path $root "dist"
$backupDir = Join-Path $root "dist.previous"
if (Test-Path -LiteralPath $backupDir) {
    # dist.previous is the last known-good build. Keep it and discard any
    # dist left behind by an interrupted build.
    if (Test-Path -LiteralPath $distDir) {
        Remove-Item -LiteralPath $distDir -Recurse -Force
    }
    Write-Warning "keeping last known-good build in dist.previous"
} elseif (Test-Path -LiteralPath $distDir) {
    Move-Item -LiteralPath $distDir -Destination $backupDir
}
Remove-Item -LiteralPath $buildDir -Recurse -Force -ErrorAction SilentlyContinue

$spec = if ($OneDir) { "vcplayer_onedir.spec" } else { "vcplayer.spec" }
try {
    & $python -m PyInstaller --noconfirm $spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

    $exe = if ($OneDir) {
        Join-Path $root "dist\vcplayer\vcplayer.exe"
    } else {
        Join-Path $root "dist\vcplayer.exe"
    }
    if (-not (Test-Path -LiteralPath $exe)) {
        throw "build output not found: $exe"
    }
    $size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
    Write-Output "OK: $exe ($size MB exe)"
    if ($OneDir) {
        $folderBytes = (Get-ChildItem -LiteralPath (Split-Path -Parent $exe) -Recurse -File |
            Measure-Object -Property Length -Sum).Sum
        $folderSize = [math]::Round($folderBytes / 1MB, 1)
        Write-Output "OK: distribution folder $folderSize MB"
    }
} catch {
    if (Test-Path -LiteralPath $distDir) {
        Remove-Item -LiteralPath $distDir -Recurse -Force
    }
    if (Test-Path -LiteralPath $backupDir) {
        Move-Item -LiteralPath $backupDir -Destination $distDir
    }
    throw
}
if (Test-Path -LiteralPath $backupDir) {
    Remove-Item -LiteralPath $backupDir -Recurse -Force
}
