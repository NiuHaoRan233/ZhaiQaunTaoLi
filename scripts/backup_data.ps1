param(
    [string]$Config = "config.toml"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Destination = Join-Path $ProjectRoot "backups\zhaiquant-$Timestamp.sqlite3"

& ".\.venv\Scripts\python.exe" -m zhaiquant --config $Config backup --output $Destination
if ($LASTEXITCODE -ne 0) {
    throw "Backup failed."
}
