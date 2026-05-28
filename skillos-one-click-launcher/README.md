# SkillOS One-Click Demo Launcher

This launcher asks for a local DeepSeek/OpenAI-compatible model configuration when needed, starts the SkillOS backend, starts the Vite frontend, verifies both endpoints, and opens the browser.

## Quick Start

From the repository root, double-click:

```text
START_SKILLOS_DEMO.bat
```

On the first run, the terminal asks for:

```text
DeepSeek API URL
DeepSeek model
DeepSeek API key
```

Press Enter to accept the default URL/model if they are correct. Paste your own API key when prompted. The key input is hidden and is saved only to `skillos-one-click-launcher\config.local.ps1`, which is ignored by Git.

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

The launcher creates `config.local.ps1` interactively if it is missing. You can also create it manually by copying:

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
$env:LLM_MODEL = "deepseek-v4-flash"
$env:LLM_API_KEY = "your-api-key"
```

`config.local.ps1` is ignored by Git. Do not commit or share it.

For non-interactive offline smoke checks only, advanced users may pass `-AllowPlaceholderLlm` to `Start-SkillOSDemo.ps1` or set `SKILLOS_ALLOW_PLACEHOLDER_LLM=1`. Normal demos should use a real key.

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
