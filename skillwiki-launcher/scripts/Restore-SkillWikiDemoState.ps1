[CmdletBinding()]
param(
    [int]$BackendPort = 0,
    [int]$FrontendPort = 0,
    [switch]$SkipApprovedImport,
    [switch]$SkipHarness,
    [switch]$SkipRelatedGraph,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $LauncherDir
$RuntimeDir = Join-Path $LauncherDir "runtime"
$PidFile = Join-Path $RuntimeDir "skillwiki-demo.pids.json"
$StartScript = Join-Path $ScriptDir "Start-SkillWikiDemo.ps1"
$RestoreScript = Join-Path $RepoRoot "scripts\restore_demo_state.py"
$ConfigFile = Join-Path $LauncherDir "config.local.ps1"

New-Item -ItemType Directory -Force $RuntimeDir | Out-Null

if (Test-Path $ConfigFile) {
    Write-Host "Loading local config: $ConfigFile"
    . $ConfigFile
}

function Resolve-SkillWikiPython {
    $candidates = @()
    if ($env:SKILLOS_PYTHON) {
        $candidates += $env:SKILLOS_PYTHON
    }
    $backendDir = Join-Path $RepoRoot "skillwiki"
    $repoVenv = Join-Path $backendDir ".venv\Scripts\python.exe"
    $candidates += @($repoVenv, "C:\Python314\python.exe", "python")

    foreach ($candidate in $candidates) {
        try {
            $cmd = Get-Command $candidate -ErrorAction Stop
            $exe = $cmd.Source
            & $exe -c "import pydantic, fastapi, uvicorn" *> $null
            if ($LASTEXITCODE -eq 0) {
                return $exe
            }
        } catch {
        }
    }

    throw "No Python runtime with required SkillWiki backend dependencies was found. Set SKILLOS_PYTHON in skillwiki-one-click-launcher\config.local.ps1."
}

function Get-LauncherState {
    if (-not (Test-Path $PidFile)) {
        return $null
    }
    try {
        return Get-Content -Path $PidFile -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-HttpOk([string]$Url) {
    try {
        $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        return ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500)
    } catch {
        return $false
    }
}

$state = Get-LauncherState
if ($BackendPort -le 0 -and $state -and $state.backendPort) {
    $BackendPort = [int]$state.backendPort
}
if ($FrontendPort -le 0 -and $state -and $state.frontendPort) {
    $FrontendPort = [int]$state.frontendPort
}
if ($BackendPort -le 0) {
    $BackendPort = 8001
}
if ($FrontendPort -le 0) {
    $FrontendPort = 5174
}

$backendHealth = "http://127.0.0.1:$BackendPort/health"
$frontendRoot = "http://127.0.0.1:$FrontendPort/"
if (-not (Test-HttpOk $backendHealth) -or -not (Test-HttpOk $frontendRoot)) {
    Write-Host "SkillWiki demo service is not fully ready. Starting backend and frontend first..."
    if (-not (Test-Path $StartScript)) {
        throw "Start script not found: $StartScript"
    }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StartScript -NoOpen
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to start SkillWiki demo service."
    }
    $state = Get-LauncherState
    if ($state -and $state.backendPort) {
        $BackendPort = [int]$state.backendPort
    }
    if ($state -and $state.frontendPort) {
        $FrontendPort = [int]$state.frontendPort
    }
}

if (-not (Test-Path $RestoreScript)) {
    throw "Restore script not found: $RestoreScript"
}

$ApiBase = "http://127.0.0.1:$BackendPort/api/v1"
$FrontendBase = "http://127.0.0.1:$FrontendPort"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$PythonExe = Resolve-SkillWikiPython

$argsList = @(
    $RestoreScript,
    "--api-base", $ApiBase,
    "--frontend-base", $FrontendBase,
    "--run-id", $Stamp
)
if ($SkipApprovedImport) {
    $argsList += "--skip-approved-import"
}
if ($SkipHarness) {
    $argsList += "--skip-harness"
}
if ($SkipRelatedGraph) {
    $argsList += "--skip-related-graph"
}

Write-Host ""
Write-Host "Restoring SkillWiki demo state..."
Write-Host "API:      $ApiBase"
Write-Host "Frontend: $FrontendBase"
Write-Host ""

& $PythonExe @argsList
if ($LASTEXITCODE -ne 0) {
    throw "SkillWiki demo-state restore failed with exit code $LASTEXITCODE"
}

$fresh = Get-Date -Format "yyyyMMddHHmmss"
$wikiUrl = "$FrontendBase/wiki?fresh=$fresh"
$graphUrl = "$FrontendBase/graph?fresh=$fresh"
$harnessUrl = "$FrontendBase/harness?fresh=$fresh"

Write-Host ""
Write-Host "Demo state restore is complete."
Write-Host "Wiki:    $wikiUrl"
Write-Host "Graph:   $graphUrl"
Write-Host "Harness: $harnessUrl"

if (-not $NoOpen) {
    Start-Process $wikiUrl
}
