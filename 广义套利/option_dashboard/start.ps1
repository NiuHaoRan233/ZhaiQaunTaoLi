param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$dashboardRoot = $PSScriptRoot
$projectRoot = Split-Path (Split-Path $dashboardRoot -Parent) -Parent
$dashboardPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dashboardData = Join-Path (Split-Path $dashboardRoot -Parent) 'data\option_dashboard'
$dashboardUrl = 'http://127.0.0.1:8766'
if (-not (Test-Path -LiteralPath $dashboardPython)) { throw "Missing Python: $dashboardPython" }
$running = $false
try {
    $response = Invoke-RestMethod "$dashboardUrl/api/catalog" -TimeoutSec 2
    $running = $null -ne $response.status.contracts
} catch { }
if (-not $running) {
    New-Item -ItemType Directory -Path $dashboardData -Force | Out-Null
    $dashboardProcess = Start-Process -FilePath $dashboardPython -ArgumentList @('-X','utf8',('"' + (Join-Path $dashboardRoot 'server.py') + '"')) -WorkingDirectory $dashboardRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $dashboardData 'server.stdout.log') -RedirectStandardError (Join-Path $dashboardData 'server.stderr.log')
    $dashboardProcess.Id | Set-Content -LiteralPath (Join-Path $dashboardData 'server.pid')
    for ($attempt=0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-RestMethod "$dashboardUrl/api/catalog" -TimeoutSec 2
            if ($null -ne $response.status.contracts) { $running=$true; break }
        } catch { }
        if ($dashboardProcess.HasExited) { break }
    }
}
if (-not $running) { throw "Dashboard did not start. See $dashboardData\server.stderr.log" }
if (-not $NoBrowser) { Start-Process $dashboardUrl }
