# SkillOS PR Upload Scope And Demo Handoff

Date: 2026-05-29  
Worktree: `C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526`

## 1. Maintainer Decision

This PR should upload reproducible code, lightweight fixtures, runner scripts, and summary documentation. It should not upload raw local benchmark runs or machine-specific runtime state.

The reason is simple: reviewers need to understand and reproduce the work. They do not need gigabytes of Docker/cache/log output, local PID files, or private API configuration.

## 2. Include In PR

### Core code

Include the backend/frontend code changes that implement:

- business-readable Skill version diffs;
- editable version fields and Version Lab;
- S2 re-verification when interface or implementation changes;
- graph visual settings and Nebula/Readable/Debug presets;
- input-to-skill metadata improvements.

Current modified paths include:

```text
skillos/skillos/api/routes/lifecycle.py
skillos/skillos/api/schemas.py
skillos/skillos/layers/input_knowledge/pipeline.py
skillos-frontend/src/api/client.ts
skillos-frontend/src/api/types.ts
skillos-frontend/src/components/AppLayout.tsx
skillos-frontend/src/pages/SkillGraph.tsx
skillos-frontend/src/pages/VersionControl.tsx
```

### Tests

Include the test additions for governance, lifecycle, input workflow, harness runner, and SkillsBench mapping:

```text
skillos/tests/test_ingest_candidate_review.py
skillos/tests/test_skill_governance_lifecycle_api.py
skillos/tests/test_input_skill_eval_runner.py
skillos/tests/test_p0_harness_eval_runner.py
skillos/tests/test_prepare_skillsbench_subset.py
skillos/tests/test_report_skillsbench_mapping.py
```

### One-click demo files

These files are small, useful, and already tracked. They should be part of the PR:

```text
README.md
START_SKILLOS_DEMO.bat
STOP_SKILLOS_DEMO.bat
RESTORE_SKILLOS_DEMO_STATE.bat
DEMO_STARTUP.md
skillos-one-click-launcher\
docs\demo-fixtures\
scripts\restore_demo_state.py
```

### Reproducible evaluation scripts

Include the scripts that let reviewers reproduce the main checks without depending on the local chat history:

```text
scripts\demo_readiness_check.py
scripts\prepare_skillsbench_subset.py
scripts\report_skillsbench_mapping.py
scripts\run_input_skill_eval.py
scripts\run_p0_harness_eval.py
```

### Documentation

Include the PR-facing reports:

```text
docs\SKILLOS_PR_UPDATE_SUMMARY_AFTER_SKILLSBENCH_20260529.md
docs\SKILLOS_SKILLSBENCH_FIVE_TASK_DEEP_ANALYSIS_20260529.md
docs\SKILLOS_PR_UPLOAD_SCOPE_AND_DEMO_HANDOFF_20260529.md
docs\SKILLOS_GROUP_OPERATION_MANUAL_FINAL_20260527.md
docs\SKILLOS_FINAL_ACCEPTANCE_AUDIT_20260528.md
docs\SKILLOS_DEMO_PAPER_GAP_REPORT_FINAL_20260527.md
docs\SKILLOS_FINAL_CLOSURE_HANDOFF_20260527.md
docs\SKILLOS_EVAL_ISOLATION_AND_BENCHMARK_PLAN_20260526.md
```

These docs are appropriate for PR because they summarize evidence and explain limitations. They are not raw logs.

## 3. Do Not Include In PR

Do not upload:

- `C:\Users\m1516\Desktop\SKILLOS\sb-runs\...` raw run directories;
- Docker Desktop installer files;
- Docker images or Docker layer cache;
- `.venv`, `node_modules`, `.pytest_cache`, build caches;
- `skillos-one-click-launcher\runtime\...`;
- `skillos-one-click-launcher\config.local.ps1`;
- copied external repositories such as full SkillsBench clones unless reduced into scripts/docs;
- API keys or model credentials;
- raw benchmark logs unless a small excerpt is needed inside a report.

The important SkillsBench results should be cited through the summary report:

```text
C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260528\SKILLOS_SKILLSBENCH_OFFICIAL_P0_SCORE_REPORT.md
```

That file lives in the local work-record area, not necessarily in the PR worktree. If reviewers need a PR-contained summary, use:

```text
docs\SKILLOS_SKILLSBENCH_FIVE_TASK_DEEP_ANALYSIS_20260529.md
```

## 4. Current Git Ignore Boundary

The current `.gitignore` already excludes the most important local-only paths:

```text
artifacts/harness-runs/
artifacts/eval-readiness-runs/
artifacts/input-skill-eval-runs/
artifacts/skillsbench-runs/
skillos-one-click-launcher/config.local.ps1
skillos-one-click-launcher/runtime/
```

This is the right boundary. Keep raw run evidence local and summarize it in docs.

## 5. Tomorrow Demo Quick Path

From Windows Explorer:

1. Open `C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526`.
2. Double-click `START_SKILLOS_DEMO.bat`.
3. On first run, fill the DeepSeek API URL, model, and API key in the startup terminal. The key is hidden and saved only to `skillos-one-click-launcher\config.local.ps1`.
4. Wait for the browser to open.
5. If the Wiki is empty because the backend uses memory storage, double-click `RESTORE_SKILLOS_DEMO_STATE.bat`.
6. Open these pages in order:

```text
http://127.0.0.1:5174/wiki
http://127.0.0.1:5174/ingest
http://127.0.0.1:5174/graph
http://127.0.0.1:5174/harness
http://127.0.0.1:5174/evaluation
http://127.0.0.1:5174/versions
```

Suggested demo storyline:

1. Wiki: show that Skill candidates and demo Skills exist.
2. Knowledge Import: show the five input types, especially `Document` and `Past Skills`.
3. Graph: switch to Nebula preset, then adjust node size/edge width.
4. Harness: show the S2-to-S3 verification idea.
5. Evaluation: show that SkillOS is not only a UI, it has evaluation evidence.
6. Version Control: show business diff and the idea that changed Skills need re-verification.
7. Explain SkillsBench result honestly: oracle `5/5`, no-skill `2/5`, SkillOS generated skills `3/5`, with `dialogue-parser` as the main win and two failures as the next repair targets.

## 6. If The Demo Does Not Start

First try:

```text
STOP_SKILLOS_DEMO.bat
START_SKILLOS_DEMO.bat
```

If Python dependencies are missing, edit:

```text
skillos-one-click-launcher\config.local.ps1
```

and set:

```powershell
$env:SKILLOS_PYTHON = "C:\path\to\python.exe"
```

If ports are busy, the launcher usually chooses the next free port automatically. The actual URL is printed in the startup window and written to:

```text
skillos-one-click-launcher\runtime\skillos-demo.pids.json
```

If the UI opens but data is empty, run:

```text
RESTORE_SKILLOS_DEMO_STATE.bat
```

If real LLM planning is needed, create:
Normally this is created by the startup prompt. To edit it manually, create:

```text
skillos-one-click-launcher\config.local.ps1
```

from:

```text
skillos-one-click-launcher\config.example.ps1
```

and fill your own endpoint/model/key. Never commit `config.local.ps1`.
