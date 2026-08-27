# Launch video-compare-player with the project venv.
# Usage: .\scripts\run.ps1 [left.mp4] [right.mp4] [--debug]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = "$root\src"
& "$root\.venv\Scripts\python.exe" -m vcplayer @CliArgs
exit $LASTEXITCODE
