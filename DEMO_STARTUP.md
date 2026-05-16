# SkillOS Demo Startup

This repository includes a Windows one-click demo launcher for the integrated SkillOS backend and frontend.

## One-Click Startup

From the repository root, double-click:

```text
START_SKILLOS_DEMO.bat
```

The launcher will:

- start the FastAPI backend on `127.0.0.1:8001`
- start the Vite frontend on `127.0.0.1:5174`
- proxy frontend API requests to the backend
- open the browser at `/wiki`
- write logs and PID state under `skillos-one-click-launcher\runtime`

To stop both services, double-click:

```text
STOP_SKILLOS_DEMO.bat
```

## Restore Demo State

The default launcher uses the `memory` backend, so imported demo candidates are
cleared after a backend restart. To restore the public demo fixtures, double-click:

```text
RESTORE_SKILLOS_DEMO_STATE.bat
```

The restore script imports the small public fixtures under `docs\demo-fixtures`,
runs the harness checks for the two S3 demo examples, and validates the related
Skill graph pack.

## Manual Startup

Backend:

```powershell
cd .\skillos
python -m skillos.api.main --host 127.0.0.1 --port 8001 --repository-backend memory
```

Frontend:

```powershell
cd .\skillos-frontend
$env:SKILLOS_API_TARGET = "http://127.0.0.1:8001"
$env:VITE_SKILLOS_DISABLE_WS = "1"
npm run dev -- --host 127.0.0.1 --port 5174
```

Open:

```text
http://127.0.0.1:5174/wiki
```

## LLM Configuration

For UI-only demos, no real LLM key is required. The launcher supplies placeholder values when no key is configured.

For real LLM planning, copy:

```text
skillos-one-click-launcher\config.example.ps1
```

to:

```text
skillos-one-click-launcher\config.local.ps1
```

Then fill your own endpoint and key:

```powershell
$env:LLM_API_URL = "https://api.deepseek.com"
$env:LLM_MODEL = "your-model-id"
$env:LLM_API_KEY = "your-api-key"
```

`config.local.ps1` is ignored by Git and must not be committed.

## Useful Demo Pages

```text
http://127.0.0.1:5174/wiki
http://127.0.0.1:5174/graph
http://127.0.0.1:5174/evaluation
http://127.0.0.1:5174/execution
http://127.0.0.1:5174/evolution
http://127.0.0.1:5174/versions
```

## Full Operator Guide

For the group-demo checklist, API configuration, fixture import steps, and
troubleshooting, see:

```text
docs\SKILLOS_DEMO_OPERATOR_GUIDE_20260516.md
```

For the detailed update report since the previous demo-paper PR baseline, see:

```text
docs\SKILLOS_DEMO_PAPER_UPDATE_REPORT_20260516.md
```
