# SkillOS Demo Handoff Package

Date: 2026-05-10 11:11 local time

This package is a local-only integration build for reproducing the previously recorded SkillOS web demo. It is intended for teammate handoff and local testing. It has not been pushed to GitHub.

## Source Summary

- Fresh clone source: `https://github.com/Huangdingcheng/skillos.git`
- Base branch: `origin/main` at `a662d7c`
- Local integration branch: `handoff/demo-recording-compatible-20260510`
- Recording-compatible baseline, read-only from the old local repo:
  - `C:\Users\m1516\Desktop\SKILLOS\skillos`
  - branch `abcde-local-preview-20260506`
  - commit `4977519 fix: integrate graph ui preview polish`
- GitHub PR refs fetched for traceability:
  - PR #1 `66f6de2` frontend-dev
  - PR #2 `847d782` agents-dev
  - PR #3 `35f4cb8` governance-dev
  - PR #4 `ec0e463` repository/repo-dev
  - PR #6 `0ed5e81` repo-version-pr
  - PR #7 `cdd9b93` runtime-dev/C runtime recovery

## Integration Notes

- The recorded demo baseline already contains the effective A/B/C/D/E integration commits used for the previous webpage demo.
- PR #4 repository head contributed graph visualization metadata commits.
- PR #3 governance contributed `fix: harden skill snapshot paths`.
- PR #7 runtime contributed optional PostgreSQL execution history persistence. The demo still starts without PostgreSQL and falls back to in-memory execution history.
- PR #1 and PR #2 are independent module PR histories based on older `main`. Directly merging their heads would remove or downgrade repository/governance/runtime files already present in the demo baseline, so their effective demo changes are kept through the recorded demo baseline rather than replaying their full old branch history.
- No remote branch, PR, tag, or GitHub state was modified while making this package.

## Start Backend

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\handoff-packages\skillos-demo-handoff-20260510\skillos
pip install -r requirements.txt
$env:SKILLOS_FORCE_PLANNER_FALLBACK='1'
python -m skillos.api.main --api-key demo --port 8000 --repository-backend memory
```

Expected health check:

```text
http://127.0.0.1:8000/health
```

The backend should run with the memory backend and fallback planner. PostgreSQL is optional; if it is unavailable, execution history stays in memory.

## Start Frontend

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\handoff-packages\skillos-demo-handoff-20260510\skillos-frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

## Recommended Demo Flow

Use the included guide:

```text
docs/demo/SKILLOS_WEB_RECORDING_MANUAL_20260506.md
```

Core route sequence:

1. Dashboard
2. Knowledge Import
3. Skill Wiki
4. Lifecycle
5. Version Control
6. Agent Execution
7. Self Evolution / Evolution
8. Skill Graph

Recommended execution goal:

```text
fill form web login click type
```

For the stable recorded-demo path, use `max_skills=3` in the execution request. Higher values may retrieve extra experimental graph test Skills and can turn the run into `partial` even though the core `fill_form -> click_element -> type_text` chain succeeds.

## Verification Commands

Backend:

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\handoff-packages\skillos-demo-handoff-20260510\skillos
python -m compileall -q skillos
python -m pytest tests\test_skill_repository_phase1.py tests\test_skill_governance_lifecycle_api.py tests\test_skill_runtime_phase1.py -q --no-cov
```

Frontend:

```powershell
cd C:\Users\m1516\Desktop\SKILLOS\handoff-packages\skillos-demo-handoff-20260510\skillos-frontend
npm install
npm run build
```

API smoke:

```text
GET  http://127.0.0.1:8000/health
GET  http://127.0.0.1:5173/api/v1/graph/stats/overview
POST http://127.0.0.1:5173/api/v1/execution/plan
```

Latest local verification in this package:

- `python -m compileall -q skillos` passed.
- `python -m pytest tests\test_skill_repository_phase1.py tests\test_skill_governance_lifecycle_api.py tests\test_skill_runtime_phase1.py -q --no-cov` passed: 21 tests.
- `npm ci --no-audit --no-fund --prefer-offline` passed.
- `npm run build` passed with the existing Vite large-chunk warning.
- Backend smoke on port `8100` with `--repository-backend memory` passed:
  - `/health` returned `{"status":"ok"}`.
  - `/api/v1/graph/stats/overview` returned 21 nodes and 5 edges.
  - `/api/v1/execution/plan` with goal `fill form web login click type` and `max_skills=3` returned `status=success` with `fill_form`, `click_element`, and `type_text`.
- Frontend dev server smoke on port `5174` returned HTTP 200 for `/`.

## Known Boundaries

- This is a local demo handoff package, not a production release.
- It uses memory backend by default for a stable recording path.
- It uses fallback planner when `SKILLOS_FORCE_PLANNER_FALLBACK=1`; real API-key LLM planning should be tested separately.
- Git-style version and diff flows are available for demo, but this package does not represent a finished GitHub collaboration workflow.
- Do not commit API keys or `.env` files into this package.
