$ErrorActionPreference = 'Stop'
try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8766/api/shutdown' -Method Post -Headers @{ 'X-Option-Dashboard'='local-stop' } -TimeoutSec 5 | Out-Null
    Write-Output 'Dashboard stopping; queued quotes are being saved.'
} catch {
    Write-Output 'Dashboard is not running or could not be reached.'
}
