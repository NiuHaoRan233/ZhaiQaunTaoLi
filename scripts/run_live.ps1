param(
    [string]$Config = "config.toml",
    [double]$DurationSeconds = 0,
    [switch]$ListReplacementTargetsOnly
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot
$Python = [IO.Path]::GetFullPath((Join-Path $ProjectRoot ".venv\Scripts\python.exe"))
$SourcePath = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "src"))
# A copied virtual environment can retain an editable-install pointer to the old
# drive.  Put this checkout first so the live runner always imports the code
# beside this script, even before setup_windows.ps1 repairs that metadata.
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $SourcePath
} else {
    $SourcePath + [IO.Path]::PathSeparator + $env:PYTHONPATH
}
$ConfigPath = if ([IO.Path]::IsPathRooted($Config)) {
    [IO.Path]::GetFullPath($Config)
} else {
    [IO.Path]::GetFullPath((Join-Path $ProjectRoot $Config))
}

function Split-WindowsCommandLine {
    param([string]$CommandLine)

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return @()
    }

    $tokens = foreach ($match in [regex]::Matches($CommandLine, '(?:[^\s"]+|"[^"]*")+')) {
        $token = $match.Value
        if ($token.Length -ge 2 -and $token[0] -eq '"' -and $token[$token.Length - 1] -eq '"') {
            $token.Substring(1, $token.Length - 2)
        } else {
            $token
        }
    }
    return @($tokens)
}

function Test-IsMatchingLiveRunner {
    param(
        $Process,
        [string]$ExpectedPython,
        [string]$ExpectedConfig
    )

    if ([string]::IsNullOrWhiteSpace($Process.ExecutablePath) -or
        -not [string]::Equals(
            [IO.Path]::GetFullPath($Process.ExecutablePath),
            $ExpectedPython,
            [StringComparison]::OrdinalIgnoreCase
        )) {
        return $false
    }

    $tokens = @(Split-WindowsCommandLine $Process.CommandLine)
    if ($tokens.Count -lt 6) {
        return $false
    }

    $moduleIndex = -1
    for ($index = 1; $index -lt ($tokens.Count - 1); $index++) {
        if ($tokens[$index] -eq '-m' -and $tokens[$index + 1] -eq 'zhaiquant') {
            $moduleIndex = $index
            break
        }
    }
    if ($moduleIndex -lt 0) {
        return $false
    }

    $configValue = $null
    $runFound = $false
    for ($index = $moduleIndex + 2; $index -lt $tokens.Count; $index++) {
        if ($tokens[$index] -eq '--config' -and $index + 1 -lt $tokens.Count) {
            $configValue = $tokens[$index + 1]
            $index++
            continue
        }
        if ($tokens[$index].StartsWith('--config=', [StringComparison]::OrdinalIgnoreCase)) {
            $configValue = $tokens[$index].Substring('--config='.Length)
            continue
        }
        if ($tokens[$index] -eq 'run') {
            $runFound = $true
        }
    }
    if (-not $runFound -or [string]::IsNullOrWhiteSpace($configValue)) {
        return $false
    }

    try {
        $processConfig = if ([IO.Path]::IsPathRooted($configValue)) {
            [IO.Path]::GetFullPath($configValue)
        } else {
            [IO.Path]::GetFullPath((Join-Path $ProjectRoot $configValue))
        }
    } catch {
        return $false
    }

    return [string]::Equals(
        $processConfig,
        $ExpectedConfig,
        [StringComparison]::OrdinalIgnoreCase
    )
}

function Get-MatchingLiveRunners {
    param([object[]]$Processes)

    return @($Processes | Where-Object {
        Test-IsMatchingLiveRunner $_ $Python $ConfigPath
    })
}

function Get-ProcessTreeForStop {
    param(
        [int]$RootProcessId,
        [object[]]$Processes
    )

    $tree = [Collections.Generic.List[object]]::new()
    $pending = [Collections.Generic.Queue[object]]::new()
    $pending.Enqueue([pscustomobject]@{ ProcessId = $RootProcessId; Depth = 0 })
    while ($pending.Count -gt 0) {
        $parent = $pending.Dequeue()
        foreach ($child in $Processes | Where-Object { $_.ParentProcessId -eq $parent.ProcessId }) {
            $entry = [pscustomobject]@{
                ProcessId = [int]$child.ProcessId
                Depth = $parent.Depth + 1
            }
            $tree.Add($entry)
            $pending.Enqueue($entry)
        }
    }
    $tree.Add([pscustomobject]@{ ProcessId = $RootProcessId; Depth = 0 })
    return @($tree | Sort-Object Depth -Descending)
}

function Stop-PreviousLiveRunners {
    $processes = @(Get-CimInstance Win32_Process)
    $runners = @(Get-MatchingLiveRunners $processes)
    if ($runners.Count -eq 0) {
        Write-Host "No previous live runner needs to be replaced."
        return
    }

    $stopEntries = foreach ($runner in $runners) {
        Get-ProcessTreeForStop ([int]$runner.ProcessId) $processes
    }
    $stopEntries = @($stopEntries |
        Sort-Object ProcessId -Unique |
        Sort-Object Depth -Descending)
    $stopIds = @($stopEntries | ForEach-Object { [int]$_.ProcessId })

    Write-Host "Replacing previous live runner process tree: $($stopIds -join ', ')"
    foreach ($entry in $stopEntries) {
        if (Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue) {
            Stop-Process -Id $entry.ProcessId -Force -ErrorAction Stop
        }
    }

    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $remaining = @($stopIds | Where-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        })
        if ($remaining.Count -eq 0) {
            Write-Host "Previous live runner stopped. Starting a new foreground instance."
            return
        }
        Start-Sleep -Milliseconds 200
    } while ([DateTime]::UtcNow -lt $deadline)

    throw "Previous live runner did not stop within 10 seconds. Remaining PIDs: $($remaining -join ', ')"
}

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run scripts\setup_windows.ps1 first."
}
if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw "Configuration not found: $Config"
}

& $Python -c "import zhaiquant"
if ($LASTEXITCODE -ne 0) {
    throw "Project import failed. Run scripts\setup_windows.ps1 to repair the virtual environment after moving the project."
}

if ($ListReplacementTargetsOnly) {
    $processes = @(Get-CimInstance Win32_Process)
    $runners = @(Get-MatchingLiveRunners $processes)
    foreach ($runner in $runners) {
        $tree = @(Get-ProcessTreeForStop ([int]$runner.ProcessId) $processes)
        [pscustomobject]@{
            RootProcessId = [int]$runner.ProcessId
            ProcessTreeIds = @($tree | ForEach-Object { [int]$_.ProcessId })
            ExecutablePath = $runner.ExecutablePath
            CommandLine = $runner.CommandLine
        }
    }
    return
}

& $Python -m zhaiquant --config $Config doctor
if ($LASTEXITCODE -ne 0) {
    throw "Doctor check failed; live collection was not started."
}

Stop-PreviousLiveRunners

if ($DurationSeconds -gt 0) {
    & $Python -m zhaiquant --config $Config run --duration-seconds $DurationSeconds
} else {
    & $Python -m zhaiquant --config $Config run
}
