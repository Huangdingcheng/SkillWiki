[CmdletBinding()]
param(
    [int]$BackendPort = 8001,
    [int]$FrontendPort = 5174,
    [ValidateSet("memory", "git")]
    [string]$RepositoryBackend = "memory",
    [string]$OpenPath = "/wiki",
    [switch]$EnableWebSocket,
    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LauncherDir = Split-Path -Parent $ScriptDir
$RepoRoot = Split-Path -Parent $LauncherDir
$BackendDir = Join-Path $RepoRoot "skillos"
$FrontendDir = Join-Path $RepoRoot "skillos-frontend"
$RuntimeDir = Join-Path $LauncherDir "runtime"
$LogDir = Join-Path $RuntimeDir "logs"
$PidFile = Join-Path $RuntimeDir "skillos-demo.pids.json"
$ConfigFile = Join-Path $LauncherDir "config.local.ps1"
$StopScript = Join-Path $ScriptDir "Stop-SkillOSDemo.ps1"

New-Item -ItemType Directory -Force $LogDir | Out-Null

if (Test-Path $ConfigFile) {
    Write-Host "Loading local config: $ConfigFile"
    . $ConfigFile
}

if ($env:SKILLOS_DEMO_BACKEND_PORT) {
    $BackendPort = [int]$env:SKILLOS_DEMO_BACKEND_PORT
}
if ($env:SKILLOS_DEMO_FRONTEND_PORT) {
    $FrontendPort = [int]$env:SKILLOS_DEMO_FRONTEND_PORT
}
if ($env:SKILLOS_DEMO_REPOSITORY_BACKEND) {
    $RepositoryBackend = $env:SKILLOS_DEMO_REPOSITORY_BACKEND
}

if (-not (Test-Path $BackendDir)) {
    throw "Backend directory not found: $BackendDir"
}
if (-not (Test-Path $FrontendDir)) {
    throw "Frontend directory not found: $FrontendDir"
}

function Resolve-SkillOSPython {
    $candidates = @()
    if ($env:SKILLOS_PYTHON) {
        $candidates += $env:SKILLOS_PYTHON
    }

    $repoVenv = Join-Path $BackendDir ".venv\Scripts\python.exe"
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

    throw "No Python runtime with required SkillOS backend dependencies was found. Set SKILLOS_PYTHON in skillos-one-click-launcher\config.local.ps1."
}

function Get-FreePort([int]$PreferredPort) {
    for ($offset = 0; $offset -lt 50; $offset++) {
        $port = $PreferredPort + $offset
        $busy = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if (-not $busy) {
            return $port
        }
    }
    throw "No free port found starting at $PreferredPort"
}

function Wait-HttpOk([string]$Url, [int]$TimeoutSeconds, [string]$Name) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "$Name did not become ready: $Url"
}

if (Test-Path $StopScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $StopScript -Silent
}

$BackendPort = Get-FreePort $BackendPort
$FrontendPort = Get-FreePort $FrontendPort

if (-not $env:LLM_API_KEY) {
    $env:LLM_API_KEY = "demo-placeholder-key"
}
if (-not $env:LLM_API_URL) {
    $env:LLM_API_URL = "https://api.deepseek.com"
}
if (-not $env:LLM_MODEL) {
    $env:LLM_MODEL = "demo-placeholder-model"
}

$PythonExe = Resolve-SkillOSPython
$NpmExe = (Get-Command npm.cmd -ErrorAction Stop).Source
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"

$BackendOut = Join-Path $LogDir "backend-$Stamp.out.log"
$BackendErr = Join-Path $LogDir "backend-$Stamp.err.log"
$FrontendOut = Join-Path $LogDir "frontend-$Stamp.out.log"
$FrontendErr = Join-Path $LogDir "frontend-$Stamp.err.log"

$backendArgs = @(
    "-m", "skillos.api.main",
    "--host", "127.0.0.1",
    "--port", [string]$BackendPort,
    "--repository-backend", $RepositoryBackend
)

if ($RepositoryBackend -eq "git") {
    $SkillStorageDir = Join-Path $RuntimeDir "skill-storage-git"
    New-Item -ItemType Directory -Force $SkillStorageDir | Out-Null
    $backendArgs += @("--skill-storage-dir", $SkillStorageDir)
}

Write-Host "Starting SkillOS backend on http://127.0.0.1:$BackendPort ..."
$backendProc = Start-Process `
    -FilePath $PythonExe `
    -ArgumentList $backendArgs `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $BackendOut `
    -RedirectStandardError $BackendErr `
    -WindowStyle Hidden `
    -PassThru

Wait-HttpOk "http://127.0.0.1:$BackendPort/health" 90 "Backend"

$env:SKILLOS_API_TARGET = "http://127.0.0.1:$BackendPort"
if ($EnableWebSocket) {
    Remove-Item Env:\VITE_SKILLOS_DISABLE_WS -ErrorAction SilentlyContinue
    $websocketDisabled = $false
} else {
    $env:VITE_SKILLOS_DISABLE_WS = "1"
    $websocketDisabled = $true
}

Write-Host "Starting SkillOS frontend on http://127.0.0.1:$FrontendPort ..."
$frontendProc = Start-Process `
    -FilePath $NpmExe `
    -ArgumentList @("run", "dev", "--", "--host", "127.0.0.1", "--port", [string]$FrontendPort) `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $FrontendOut `
    -RedirectStandardError $FrontendErr `
    -WindowStyle Hidden `
    -PassThru

Wait-HttpOk "http://127.0.0.1:$FrontendPort/" 60 "Frontend"
Wait-HttpOk "http://127.0.0.1:$FrontendPort/api/v1/skills?limit=1" 30 "Frontend proxy"

$fresh = Get-Date -Format "yyyyMMddHHmmss"
$sep = "?"
if ($OpenPath.Contains("?")) {
    $sep = "&"
}
$OpenUrl = ("http://127.0.0.1:{0}{1}{2}fresh={3}" -f $FrontendPort, $OpenPath, $sep, $fresh)

$status = [ordered]@{
    startedAt = (Get-Date).ToString("s")
    backendPid = $backendProc.Id
    frontendPid = $frontendProc.Id
    backendPort = $BackendPort
    frontendPort = $FrontendPort
    repositoryBackend = $RepositoryBackend
    websocketDisabled = $websocketDisabled
    frontendUrl = $OpenUrl
    apiTarget = $env:SKILLOS_API_TARGET
    logs = [ordered]@{
        backendOut = $BackendOut
        backendErr = $BackendErr
        frontendOut = $FrontendOut
        frontendErr = $FrontendErr
    }
}
$status | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 $PidFile

Write-Host ""
Write-Host "SkillOS demo is running."
Write-Host "Frontend: $OpenUrl"
Write-Host "Backend:  http://127.0.0.1:$BackendPort"
Write-Host "PID file: $PidFile"
Write-Host "Logs:     $LogDir"
Write-Host ""

if (-not $NoOpen) {
    Start-Process $OpenUrl
}
