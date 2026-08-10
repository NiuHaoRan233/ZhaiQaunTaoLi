param(
    [string]$PythonCommand = "python",
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if (-not (Test-Path -LiteralPath ".\.venv\Scripts\python.exe")) {
    & $PythonCommand -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\.venv\Scripts\python.exe" -m pip install -e .

if (-not (Test-Path -LiteralPath ".\config.toml")) {
    Copy-Item -LiteralPath ".\config.example.toml" -Destination ".\config.toml"
}

& ".\.venv\Scripts\python.exe" -m unittest discover -v
if (-not $SkipDoctor) {
    & ".\.venv\Scripts\python.exe" -m zhaiquant --config config.toml doctor
}

Write-Host "Setup complete. Review config.toml before starting live collection."
