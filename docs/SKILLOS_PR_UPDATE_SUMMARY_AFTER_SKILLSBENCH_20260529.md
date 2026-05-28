# SkillOS PR Update Summary After SkillsBench

Date: 2026-05-29  
Worktree: `C:\Users\m1516\Desktop\SKILLOS\skillos-pr11-version-lab-20260526`  
Companion analysis: `docs\SKILLOS_SKILLSBENCH_FIVE_TASK_DEEP_ANALYSIS_20260529.md`

## 1. Executive Summary

This PR moves SkillOS from a local demo prototype toward a demo-paper-ready system with runnable evidence. The most important change is not one isolated feature. It is that the project now has a visible workflow from input material, to Skill candidate, to graph/version management, to execution harness, and finally to an external benchmark check.

The current benchmark result should be stated carefully. On the selected 5-task SkillsBench P0 subset, oracle passes `5/5`, the no-skill baseline passes `2/5`, and SkillOS generated skills pass `3/5` using clean combined evidence. The clearest improvement is `dialogue-parser`, where the reward improves from `0.667` to `1.0`. The two remaining failures, `sales-pivot-analysis` and `software-dependency-audit`, are also useful because they show exactly what the next repair loop should target.

The main claim is therefore:

```text
SkillOS can run end to end, convert multiple input types into governed Skill candidates, show the resulting Skill graph and version history, verify selected Skills through a local harness, and produce an initial external SkillsBench result. It is not yet a full automatic skill-evolution system, but it now has enough evidence for a serious group demo and a demo-paper PR.
```

## 2. What The Team Asked For And What Is Done

| Requirement | Current status | Evidence |
| --- | --- | --- |
| The system must actually run | Done. One-click launcher, stop script, restore script, backend/frontend health checks, LLM config path, graph/evaluation/version/harness pages | `DEMO_STARTUP.md`, `skillos-one-click-launcher\README.md`, `docs\SKILLOS_FINAL_ACCEPTANCE_AUDIT_20260528.md` |
| Generated Skills should be evaluated with a benchmark | Done for a 5-task SkillsBench P0 subset. Oracle `5/5`, no-skill `2/5`, SkillOS generated skills `3/5` using clean combined evidence | `C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260528\SKILLOS_SKILLSBENCH_OFFICIAL_P0_SCORE_REPORT.md` |
| Each input type should have roughly 20-50 real samples | Done at P0 scale: 25 samples per input type, 125 total | `C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\external-test-corpus-20260527\manifests\input_skill_eval_manifest_p0_20260527.json` |
| The samples should cover multiple domains | Done. The P0 corpus covers software, web, data, office, security, science, API, and finance-like tasks | same manifest |
| Frontend graph should be better for large Skill graphs | Done. Graph now has Nebula/Readable/Debug presets and controls for node size, edge width, opacity, labels, charge, and link distance | `docs\SKILLOS_UPDATE_REPORT_SINCE_LAST_PR_20260527.md` and screenshots report |
| Git/version work from PR #11 should be considered | Done by selective adoption. Useful Git/version ideas were reimplemented on the current branch; the old PR was not merged wholesale | `C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\demo-paper-roadmap-20260509\PR11_GIT_VERSION_REVIEW_20260526.md` |

## 3. Main System Updates

### 3.1 One-click runnable demo

The repository now includes a Windows-friendly demo launcher:

- `START_SKILLOS_DEMO.bat`
- `STOP_SKILLOS_DEMO.bat`
- `RESTORE_SKILLOS_DEMO_STATE.bat`
- `skillos-one-click-launcher\scripts\Start-SkillOSDemo.ps1`
- `skillos-one-click-launcher\scripts\Stop-SkillOSDemo.ps1`
- `skillos-one-click-launcher\scripts\Restore-SkillOSDemoState.ps1`

The launcher starts the FastAPI backend and Vite frontend, checks that both are reachable, writes logs under `skillos-one-click-launcher\runtime`, and opens the browser. The local secret file `skillos-one-click-launcher\config.local.ps1` is ignored by Git. This is important because group members can configure DeepSeek or another OpenAI-compatible endpoint without leaking API keys into the repo.

### 3.2 Five input types to Skill candidates

The five supported input types are:

- `trajectory`
- `document`
- `api_doc`
- `script`
- `past_skills`

The P0 corpus contains 25 examples per type, for 125 total. Each fixture has source metadata, domain labels, expected Skill shape, and license/source notes. The tested workflow is:

```text
fixture
-> parse
-> audit
-> create S1 candidate
-> graph check
-> lifecycle diff/history
-> snapshot
-> report
```

This matters because the system is no longer only a parser demo. It now checks whether imported material enters the Skill lifecycle, graph, and version-management paths.

### 3.3 Ctx2Skill-lite and Past Skills import

The document path was upgraded from simple summarization to a Ctx2Skill-inspired process. In practical terms, that means the system tries to extract not only a summary, but also tasks, constraints, evidence, and possible failure signals from the input context.

The `past_skills` path is also important. It allows existing Skill-like material, such as Anthropic-style skills or older internal skills, to be normalized into the SkillOS schema. These imported Skills are classified into `atomic`, `functional`, or `strategic` layers, following the SkillX-style idea that not all skills live at the same abstraction level.

### 3.4 Git/version integration from PR #11

PR #11 was not merged directly. That was the correct decision because it was based on an older code state and would have lost or conflicted with current harness, evaluation, graph, and governance work.

The useful ideas were still adopted:

- business-readable version diff;
- editable version fields;
- Version Lab UI;
- re-verification when interface or implementation changes.

The backend now supports more meaningful version requests and diff summaries. The frontend Version Lab lets a user inspect a Skill, edit a new version, preview differences, create snapshots, and route changed Skills back toward verification.

### 3.5 Frontend graph improvements

The graph page now works better for demo and inspection. The graph has three presets:

- `Nebula`: small nodes and light labels for large graphs;
- `Readable`: more labels for explanation;
- `Debug`: stronger relation visibility for checking graph quality.

Users can tune node size, edge width, edge opacity, label display, edge labels, force charge, link distance, and dense mode. This directly addresses the request that the graph should look more like a usable Skill constellation rather than a crowded debugging view.

## 4. Benchmark Evidence

The selected SkillsBench P0 subset contains:

- `citation-check`
- `court-form-filling`
- `dialogue-parser`
- `sales-pivot-analysis`
- `software-dependency-audit`

Final score summary:

| Condition | Evidence scope | Passed | Total | Score |
| --- | --- | ---: | ---: | ---: |
| Oracle | full all5 run | 5 | 5 | 100.0% |
| No skill | full all5 run | 2 | 5 | 40.0% |
| SkillOS generated skills | clean combined evidence | 3 | 5 | 60.0% |

The SkillOS generated score must be described precisely. It combines the clean first four tasks from one all5 run with a clean single-task rerun for `software-dependency-audit`. The all5 SkillOS run had a verifier timeout on task 5, so that task-5 result was not counted. The later single-task rerun had no agent error and no verifier error, and it failed on exact CSV ground-truth matching.

This is acceptable evidence for analysis and group demo, but it should not be advertised as one uninterrupted all5 generated-skill artifact.

## 5. What The Papers Helped With

This project uses papers and systems as design guidance, not as a full reproduction of each paper.

SkillX is reflected in the Skill layer design: imported material can become atomic, functional, or strategic Skills. This worked especially well for `past_skills` and for the dialogue graph example.

Ctx2Skill is reflected in the document-to-skill path. The system extracts tasks, rubrics, constraints, and evidence-like fields from context. This helped most clearly on `dialogue-parser`, where structural constraints from the input became useful guidance.

SkillsBench provides the external verifier. It is the reason we can say which tasks improved and which failed, instead of only judging by whether the generated Skill looks reasonable.

SKILLFOUNDRY motivates the lifecycle framing: resources become Skill candidates, then need provenance, tests, validation, and version history. SkillOS now has enough of this loop to demonstrate the idea.

Reflexion, ExpeL, and SkillClaw point to the next step: failure should not just be recorded; it should drive Skill repair. The current system has the failure evidence, but the fully automatic repair loop is still future work.

HIN and GraphRAG motivate the heterogeneous graph view. SkillOS can now show Skills, sources, provenance, and version/evidence relations, but graph quality still needs stronger automatic scoring.

## 6. What Still Needs Improvement

The most important limitation is not that two SkillsBench tasks failed. The important limitation is why they failed.

`sales-pivot-analysis` shows that SkillOS can guide data processing, but it does not yet guarantee low-level Excel pivot metadata such as cache fields, row fields, and column fields. The next fix should be a code-grounded xlsx Skill with a local verifier.

`software-dependency-audit` shows that SkillOS can guide vulnerability scanning and CSV generation, but it does not yet guarantee exact benchmark normalization, such as URL priority, fixed-version formatting, and title text. The next fix should be an exact-output normalizer plus a verifier-aware security audit Skill.

More broadly, SkillOS currently generates useful Skill candidates and can verify selected examples, but it does not yet automatically turn every benchmark failure into a repaired S3 Skill. That is the next demo-paper-strengthening direction.

## 7. PR Inclusion Recommendation

Include in the PR:

- backend and frontend code changes for version diff, Version Lab, graph settings, and ingestion fixes;
- tests for lifecycle/version and input-to-skill workflow;
- one-click launcher scripts and demo fixtures;
- reproducible scripts under `scripts\`, including readiness, input-skill evaluation, harness evaluation, and SkillsBench subset/mapping helpers;
- documentation reports under `docs\`, including this update summary and the five-task analysis.

Do not include in the PR:

- local `sb-runs` raw benchmark output directories;
- Docker Desktop installer or Docker images;
- `.venv`, `node_modules`, runtime logs, PID files, cache directories;
- `skillos-one-click-launcher\config.local.ps1`;
- any API key or local-only model credential;
- large external corpus downloads unless they are reduced into small manifests or demo fixtures.

The current `.gitignore` already excludes runtime harness evidence, readiness runs, input-skill eval runs, SkillsBench run artifacts, launcher runtime state, and local launcher secrets. That is the right boundary for this PR.

## 8. Suggested PR Body

```text
Summary
- Added Windows one-click demo startup/stop/restore flow for SkillOS.
- Added/validated a P0 five-input corpus workflow: trajectory, document, api_doc, script, and past_skills.
- Added Ctx2Skill-lite evidence and SkillX-style layer metadata to candidate review.
- Integrated selected PR #11 Git/version ideas as current-branch features: business diff, editable version fields, Version Lab, and re-verification on implementation/interface changes.
- Improved the graph page with Nebula/Readable/Debug presets and node/edge/label controls.
- Connected generated SkillOS skills to a 5-task SkillsBench P0 subset. Oracle: 5/5, no-skill: 2/5, SkillOS generated skills: 3/5 using clean combined evidence.

Verification
- Readiness: 15/15.
- Backend representative tests: 135 passed.
- Python compileall: passed.
- Frontend lint/build: passed.
- Input workflow: 125 candidates created in P0 evaluation.
- Local harness representative run: positive pass 1.0, negative rejection 1.0.
- SkillsBench P0 subset: oracle 5/5, no-skill 2/5, generated SkillOS skills 3/5.
- Secret scan: no source/final-doc key matches.

Limitations
- The generated-skill SkillsBench score is clean combined evidence, not one uninterrupted all5 generated-skill run.
- Ctx2Skill is implemented as a demo-paper Ctx2Skill-lite, not full large-scale self-play.
- The two benchmark failures show that xlsx pivot metadata and exact security CSV normalization need verifier-aware repair Skills.
```
