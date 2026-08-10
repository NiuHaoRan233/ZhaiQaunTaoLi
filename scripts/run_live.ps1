param(
    [string]$Config = "config.toml",
    [double]$DurationSeconds = 0
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$Python = ".\.venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run scripts\setup_windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Configuration not found: $Config"
}

& $Python -m zhaiquant --config $Config doctor
if ($LASTEXITCODE -ne 0) {
    throw "Doctor check failed; live collection was not started."
}

if ($DurationSeconds -gt 0) {
    & $Python -m zhaiquant --config $Config run --duration-seconds $DurationSeconds
} else {
    & $Python -m zhaiquant --config $Config run
}
