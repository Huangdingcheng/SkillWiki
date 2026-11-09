# SkillWiki One-Click Demo Launcher

This launcher asks for a local DeepSeek/OpenAI-compatible model configuration when needed, starts the SKILLWIKI backend, starts the Vite frontend, verifies both endpoints, and opens the browser.

## Quick Start

From the repository root, double-click:

```text
START_SKILLWIKI_DEMO.bat
```

On the first run, the terminal asks for:

```text
DeepSeek API URL
DeepSeek model
DeepSeek API key
```

Press Enter to accept the default URL/model if they are correct. Paste your own API key when prompted. The key input is hidden and is saved only to `skillwiki-launcher\config.local.ps1`, which is ignored by Git.

To stop the demo, double-click:

```text
STOP_SKILLWIKI_DEMO.bat
```

Skills and their real dependency graph survive restarts in the default Git backend. To explicitly import public demo fixtures, double-click:

```text
RESTORE_SKILLWIKI_DEMO_STATE.bat
```

Default local URLs:

```text
Frontend: http://127.0.0.1:5174/wiki
Backend:  http://127.0.0.1:8001
```

## Local LLM Configuration

The launcher creates `config.local.ps1` interactively if it is missing. You can also create it manually by copying:

```text
skillwiki-launcher\config.example.ps1
```

to:

```text
skillwiki-launcher\config.local.ps1
```

Then fill:

```powershell
$env:LLM_API_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "deepseek-flash"
$env:LLM_API_KEY = "your-api-key"
```

`config.local.ps1` is ignored by Git. Do not commit or share it.

For non-interactive offline smoke checks only, advanced users may pass `-AllowPlaceholderLlm` to `Start-SkillWikiDemo.ps1` or set `SKILLOS_ALLOW_PLACEHOLDER_LLM=1`. Normal demos should use a real key.

## Defaults

- Backend port: `8001`
- Frontend port: `5174`
- Repository backend: `git`, stored in `runtime/skill-storage-git`
- Governance snapshots: `runtime/governance-git` unless `SKILLOS_GOVERNANCE_REPO` is set
- Synthetic demo fixtures: disabled; use `-SeedDemo` to add them explicitly
- LLM model: `deepseek-flash`; retries: `0`
- WebSocket: disabled by default for a more stable Windows local demo
- Open page: `/wiki`

## Advanced Usage

Run from PowerShell:

```powershell
.\skillwiki-launcher\scripts\Start-SkillWikiDemo.ps1 -OpenPath /evaluation
```

For an explicitly authorized M1 model smoke run, add `-EnableM1Budget`. This sets a CNY 1 M1 ceiling, CNY 10 shared ledger ceiling, scope `m1`, and 2048 maximum output tokens using `runtime/llm-budget.jsonl`. The switch only configures admission limits; starting the launcher does not itself make a model call. The same settings can be supplied via `SKILLWIKI_LLM_BUDGET_LIMIT_CNY`, `SKILLWIKI_LLM_BUDGET_TOTAL_LIMIT_CNY`, `SKILLWIKI_LLM_BUDGET_LEDGER_PATH`, `SKILLWIKI_LLM_BUDGET_SCOPE`, and `SKILLWIKI_LLM_BUDGET_MAX_OUTPUT_TOKENS`.

Use `-RepositoryBackend memory` only for disposable sessions. Existing Git skills are reused, and enabling `-SeedDemo` adds only missing fixture identities without replacing existing versions.

Stop silently:

```powershell
.\skillwiki-launcher\scripts\Stop-SKILLWIKIDemo.ps1 -Silent
```

Runtime logs and PID state are written under:

```text
skillwiki-launcher\runtime
```

Demo-state restore reports are also written under:

```text
skillwiki-launcher\runtime\demo-state-runs
```
