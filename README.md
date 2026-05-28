# SkillOS

SkillOS is a skill-centric agent operating system prototype. It can import experience from multiple source types, convert that material into governed Skill candidates, show Skill graph relations, manage Skill versions, and run local verification/evaluation workflows.

This branch is packaged for a Windows one-click demo. After dependencies are installed, run one file. On the first run, the launcher asks for your own DeepSeek-compatible model configuration, saves it to a local ignored config file, starts the backend and frontend, and opens the web UI.

## Quick Start On Windows

### 1. Install Dependencies

Backend:

```powershell
cd <repo-root>\skillos
python -m pip install -r requirements.txt
```

Frontend:

```powershell
cd <repo-root>\skillos-frontend
npm install
```

### 2. Start SkillOS

Open the repository root and double-click:

```text
START_SKILLOS_DEMO.bat
```

On the first run, the terminal asks for:

```text
DeepSeek API URL
DeepSeek model
DeepSeek API key
```

Press Enter to accept the default URL/model if they are correct:

```text
https://api.deepseek.com
deepseek-v4-flash
```

Paste your own API key when prompted. The key input is hidden. The launcher writes the values only to:

```text
skillos-one-click-launcher\config.local.ps1
```

That file is ignored by Git and must not be committed.

After configuration, the launcher starts:

```text
Backend:  http://127.0.0.1:8001
Frontend: http://127.0.0.1:5174/wiki
```

If those ports are busy, the launcher chooses nearby free ports and prints the actual URL.

### 3. Restore Demo Data

The default demo uses the memory backend, so data is cleared after restart. To restore the small public demo fixtures, double-click:

```text
RESTORE_SKILLOS_DEMO_STATE.bat
```

### 4. Stop SkillOS

Double-click:

```text
STOP_SKILLOS_DEMO.bat
```

## Useful Pages

```text
http://127.0.0.1:5174/wiki
http://127.0.0.1:5174/ingest
http://127.0.0.1:5174/graph
http://127.0.0.1:5174/harness
http://127.0.0.1:5174/evaluation
http://127.0.0.1:5174/versions
```

Recommended demo order:

1. Skill Wiki
2. Knowledge Import
3. Knowledge Graph
4. Harness Verification
5. Evaluation
6. Version Control

## What This Branch Demonstrates

- Five input types: `trajectory`, `document`, `api_doc`, `script`, and `past_skills`.
- Ctx2Skill-lite evidence for document-to-skill extraction.
- SkillX-style layer metadata: `atomic`, `functional`, and `strategic`.
- Skill graph visualization with Nebula/Readable/Debug presets.
- Version Lab with business-readable diffs and re-verification after implementation/interface changes.
- Local harness verification for selected Skills.
- SkillsBench P0 sparse-subset analysis: oracle `5/5`, no-skill `2/5`, SkillOS generated skills `3/5` using clean combined evidence.

## Documentation

Start here:

```text
DEMO_STARTUP.md
docs\SKILLOS_PR_UPLOAD_SCOPE_AND_DEMO_HANDOFF_20260529.md
docs\SKILLOS_PR_UPDATE_SUMMARY_AFTER_SKILLSBENCH_20260529.md
docs\SKILLOS_SKILLSBENCH_FIVE_TASK_DEEP_ANALYSIS_20260529.md
```

## PR Safety Notes

Commit code, lightweight fixtures, scripts, and documentation. Do not commit:

- `skillos-one-click-launcher\config.local.ps1`
- `skillos-one-click-launcher\runtime\`
- raw benchmark run directories
- Docker installers/images/cache
- `.venv`, `node_modules`, or build caches
- API keys or local credentials
