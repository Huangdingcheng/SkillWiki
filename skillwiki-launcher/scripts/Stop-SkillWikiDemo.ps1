[CmdletBinding()]
param(
    [switch]$Silent
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherDir = Split-Path -Parent $ScriptDir
$RuntimeDir = Join-Path $LauncherDir "runtime"
$PidFile = Join-Path $RuntimeDir "skillwiki-demo.pids.json"

function Write-Status([string]$Message) {
    if (-not $Silent) {
        Write-Host $Message
    }
}

function Stop-ProcessTree([int]$ProcessId) {
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree ([int]$child.ProcessId)
    }

    $proc = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Stop-ListenerOnPort([int]$Port) {
    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    foreach ($listener in $listeners) {
        $ownerPid = [int]$listener.OwningProcess
        if ($ownerPid -le 0) {
            continue
        }
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$ownerPid" -ErrorAction SilentlyContinue
        if ($proc -and $proc.CommandLine -match "skillwiki\.api\.main|vite|npm-cli") {
            Stop-ProcessTree $ownerPid
        }
    }
}

$ports = New-Object System.Collections.Generic.List[int]

if (Test-Path $PidFile) {
    try {
        $state = Get-Content $PidFile -Raw | ConvertFrom-Json
        if ($state.backendPid) {
            Write-Status "Stopping backend PID $($state.backendPid) ..."
            Stop-ProcessTree ([int]$state.backendPid)
        }
        if ($state.frontendPid) {
            Write-Status "Stopping frontend PID $($state.frontendPid) ..."
            Stop-ProcessTree ([int]$state.frontendPid)
        }
        if ($state.backendPort) {
            $ports.Add([int]$state.backendPort)
        }
        if ($state.frontendPort) {
            $ports.Add([int]$state.frontendPort)
        }
    } catch {
        Write-Status "Could not read PID file cleanly; falling back to port cleanup."
    }
}

$ports.Add(8001)
$ports.Add(5174)
$ports.Add(8000)
$ports.Add(5173)

foreach ($port in ($ports | Select-Object -Unique)) {
    Stop-ListenerOnPort $port
}

if (Test-Path $PidFile) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

Write-Status "SkillWiki demo processes stopped."
