# SkillOS Evaluation Isolation and Benchmark Plan

Date: 2026-05-26

This document records the evaluation rules for the next SkillOS closure phase. The goal is to satisfy the group lead's requirements without polluting reusable test corpora or overstating partial API checks as full benchmark evidence.

## Isolation Rules

- Raw external fixtures are read-only. Do not write generated candidates, repaired skills, logs, or benchmark outputs back into `docs/demo-fixtures` or any `raw`/`selected` corpus directory.
- Every run writes to a derived run directory, for example `artifacts/eval-readiness-runs/readiness-<run-id>` or `Codex-skilos/artifacts/eval-YYYYMMDD/<run-id>`.
- Imported SkillOS candidates must carry stable provenance: `source_id`, `input_type`, `domain`, and a content hash.
- Repeated runs must be idempotent. A runner should skip or update report rows for already imported samples instead of creating duplicate Skills.
- Memory backend is acceptable for a demo restore. Git backend should be used for persistent evaluation evidence.
- SkillsBench runs must use an isolated clone/workspace and must not modify the curated SkillOS fixture corpus.

## Required Evaluation Layers

The final evaluation is not allowed to test only a narrow SkillOS endpoint. It must cover the full user-visible workflow:

```text
fixture
-> /ingest/parse
-> candidate audit
-> /ingest/create-candidate
-> graph write check
-> version snapshot / business diff check
-> harness positive and negative checks
-> benchmark mapping
-> report
```

For SkillsBench / BenchFlow, the order is:

```text
official task check
-> oracle sanity run
-> no-skill baseline
-> generated-skill run
-> verifier-backed comparison report
```

If an input sample cannot map cleanly to an official SkillsBench task, it remains in the local harness full set and is marked as `skillsbench_mapped=false`.

## P0 Dataset Target

P0 uses 25 fixtures for each source type:

- `trajectory`
- `document`
- `api_doc`
- `script`
- `past_skills`

Each source type should cover at least five domains. Each domain should have at least three fixtures where practical.

P1 expands each source type to 50 fixtures after P0 runners are stable.

## Manifest Contract

Each fixture must have a manifest row:

```json
{
  "source_id": "stable-id",
  "input_type": "document",
  "domain": "software",
  "source_url": "https://example.com/source",
  "paper_or_project": "SkillsBench / Ctx2Skill / WebArena / Anthropic Skills / browser-use",
  "expected_skill_shape": "atomic",
  "license_note": "public docs / paper sample / benchmark fixture",
  "target_benchmark_tasks": [],
  "local_verifier_expectations": []
}
```

## Current Readiness Entrypoint

Use:

```powershell
python scripts\demo_readiness_check.py `
  --api-base http://127.0.0.1:8001/api/v1 `
  --frontend-base http://127.0.0.1:5174 `
  --run-id <name>
```

For non-mutating service checks only:

```powershell
python scripts\demo_readiness_check.py --skip-mutating-probe --run-id readonly-check
```

The readiness script writes all request/response evidence under `artifacts/eval-readiness-runs` and never modifies raw fixture directories.

## Five-Input Evaluation Runner

Use `scripts/run_input_skill_eval.py` for reusable manifest-driven tests.

Parse-only, no Wiki writes:

```powershell
python scripts\run_input_skill_eval.py `
  --manifest C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260515\manifests\input_skill_eval_manifest_p0_smoke_20260526.json `
  --fixture-root C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260515 `
  --api-base http://127.0.0.1:8001/api/v1 `
  --run-id parse-only-smoke `
  --limit-per-type 1
```

Full local SkillOS workflow, explicitly mutating a disposable eval backend:

```powershell
python scripts\run_input_skill_eval.py `
  --manifest <manifest.json> `
  --fixture-root <fixture-root> `
  --api-base http://127.0.0.1:8001/api/v1 `
  --run-id full-workflow-001 `
  --create-candidates `
  --snapshot
```

The runner records per-fixture parse, audit, create, graph, version, harness, and SkillsBench fields. SkillsBench stays `not_run` until the benchmark adapter actually executes; do not fill it manually.

## SkillsBench Subset Preparation

The full SkillsBench repository is large. As of the 2026-05-26 API check, GitHub reports a repository size around 876,828 KB, so do not clone it wholesale for routine SkillOS testing.

Prepare a lightweight subset instead:

```powershell
python scripts\prepare_skillsbench_subset.py `
  --run-id p0-subset `
  --task citation-check `
  --task sales-pivot-analysis `
  --task software-dependency-audit `
  --task court-form-filling `
  --task dialogue-parser
```

Dry-run mode lists what would be downloaded without writing task files:

```powershell
python scripts\prepare_skillsbench_subset.py --run-id dry-smoke --dry-run --task citation-check
```

The subset script writes to `artifacts/skillsbench-runs/skillsbench-subset-<run-id>` and creates `skillsbench_subset_manifest.json` plus `RUN_COMMANDS.md`. Official checks still need to run inside that isolated subset workspace:

```powershell
uv sync --locked
uv run bench tasks check tasks/<task-id>
uv run bench eval create -t tasks/<task-id> -a oracle
```

Only after the oracle sanity run passes should SkillOS compare `no_skill` versus `generated_skill`.

Practical note from the first 2026-05-26 probe: GitHub Contents API access can hit rate limits while recursively listing task directories. If this happens, stop the run, delete the incomplete `skillsbench-subset-<run-id>` directory, and retry later with `--max-files-per-task`, or switch to a sparse checkout of only `README.md`, `pyproject.toml`, `uv.lock`, `taxonomy.*`, and the selected `tasks/<task-id>` directories.
