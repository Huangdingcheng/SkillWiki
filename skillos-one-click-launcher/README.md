# SkillOS One-Click Demo Launcher

This launcher starts the SkillOS backend, starts the Vite frontend, verifies both endpoints, and opens the browser.

## Quick Start

From the repository root, double-click:

```text
START_SKILLOS_DEMO.bat
```

To stop the demo, double-click:

```text
STOP_SKILLOS_DEMO.bat
```

To restore public demo fixtures after a memory-backend restart, double-click:

```text
RESTORE_SKILLOS_DEMO_STATE.bat
```

Default local URLs:

```text
Frontend: http://127.0.0.1:5174/wiki
Backend:  http://127.0.0.1:8001
```

## Local LLM Configuration

To use a real OpenAI-compatible model, copy:

```text
skillos-one-click-launcher\config.example.ps1
```

to:

```text
skillos-one-click-launcher\config.local.ps1
```

Then fill:

```powershell
$env:LLM_API_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "your-model-id"
$env:LLM_API_KEY = "your-api-key"
```

`config.local.ps1` is ignored by Git. Do not commit or share it.

## Defaults

- Backend port: `8001`
- Frontend port: `5174`
- Repository backend: `memory`
- WebSocket: disabled by default for a more stable Windows local demo
- Open page: `/wiki`

## Advanced Usage

Run from PowerShell:

```powershell
.\skillos-one-click-launcher\scripts\Start-SkillOSDemo.ps1 -RepositoryBackend git -OpenPath /evaluation
```

Stop silently:

```powershell
.\skillos-one-click-launcher\scripts\Stop-SkillOSDemo.ps1 -Silent
```

Runtime logs and PID state are written under:

```text
skillos-one-click-launcher\runtime
```

Demo-state restore reports are also written under:

```text
skillos-one-click-launcher\runtime\demo-state-runs
```
