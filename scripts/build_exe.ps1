# Build the single-file vcplayer.exe into dist\.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "venv python not found: $python"
}

& $python -m pip install --quiet "pyinstaller>=6.10"
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }

Remove-Item -LiteralPath (Join-Path $root "build") -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path $root "dist") -Recurse -Force -ErrorAction SilentlyContinue

& $python -m PyInstaller --noconfirm vcplayer.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }

$exe = Join-Path $root "dist\vcplayer.exe"
$size = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
Write-Output "OK: $exe ($size MB)"
