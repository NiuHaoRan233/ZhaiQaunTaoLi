param(
    [string]$PythonCommand = "python",
    [switch]$SkipDoctor
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$VenvPython = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv\Scripts\python.exe"))

if (-not (Test-Path -LiteralPath $VenvPython)) {
    & $PythonCommand -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment."
    }
}

& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "Failed to upgrade pip in the virtual environment."
}
& $VenvPython -m pip install -e .
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the current project into the virtual environment."
}
& $VenvPython -c "from pathlib import Path; import zhaiquant; expected=(Path.cwd() / 'src' / 'zhaiquant').resolve(); actual=Path(zhaiquant.__file__).resolve().parent; assert actual == expected, f'zhaiquant is loaded from {actual}, expected {expected}'"
if ($LASTEXITCODE -ne 0) {
    throw "Editable installation does not point to the current project directory."
}

if (-not (Test-Path -LiteralPath ".\config.toml")) {
    Copy-Item -LiteralPath ".\config.example.toml" -Destination ".\config.toml"
}

& $VenvPython -m unittest discover -v
if ($LASTEXITCODE -ne 0) {
    throw "Core test suite failed."
}
if (-not $SkipDoctor) {
    & $VenvPython -m zhaiquant --config config.toml doctor
    if ($LASTEXITCODE -ne 0) {
        throw "MiniQMT read-only doctor check failed."
    }
}

Write-Host "Setup complete. Review config.toml before starting live collection."
