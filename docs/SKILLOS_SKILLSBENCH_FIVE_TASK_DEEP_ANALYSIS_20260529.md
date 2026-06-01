# SkillOS SkillsBench Five-Task Deep Analysis

Date: 2026-05-29  
Benchmark scope: selected SkillsBench P0 sparse subset, 5 tasks  
Score source: `C:\Users\m1516\Desktop\SKILLOS\Codex-skilos\artifacts\eval-20260528\SKILLOS_SKILLSBENCH_OFFICIAL_P0_SCORE_REPORT.md`

## 1. Purpose Of This Report

This report does not rerun SkillsBench. It reviews the already completed runs and explains what they mean.

The goal is to answer three questions in a way that is useful for a PR, a group demo, and the next implementation step:

1. Which tasks succeeded or failed?
2. Which paper-inspired SkillOS abilities helped or did not help?
3. What should be improved next?

The main result is:

| Condition | Evidence scope | Passed | Total | Score |
| --- | --- | ---: | ---: | ---: |
| Oracle | full all5 run | 5 | 5 | 100.0% |
| No skill | full all5 run | 2 | 5 | 40.0% |
| SkillOS generated skills | clean combined evidence | 3 | 5 | 60.0% |

The SkillOS result is clean combined evidence, not a single uninterrupted all5 generated-skill run. The first four task results come from one all5 run. The fifth task, `software-dependency-audit`, was rerun alone after a verifier timeout in the all5 run. The rerun completed cleanly and failed for a real answer-quality reason.

## 2. Overall Interpretation

SkillOS helped most when the task needed structure: extracting nodes, edges, constraints, steps, and validation rules from text-like material. This is why `dialogue-parser` is the strongest result.

SkillOS helped less when the task needed exact low-level artifacts. `sales-pivot-analysis` required correct Excel pivot metadata, not just a workbook that looks right. `software-dependency-audit` required exact CSV normalization, not just a plausible vulnerability report.

This is a useful result. It shows that the current system is already meaningful for demo-paper work, while also identifying precise repair targets.

## 3. Score Table

| Task | No Skill | SkillOS Generated Skills | Change | Interpretation |
| --- | ---: | ---: | ---: | --- |
| `citation-check` | 1.0 | 1.0 | 0.0 | No regression, but no clear improvement |
| `court-form-filling` | 1.0 | 1.0 | 0.0 | No regression, but baseline was already strong |
| `dialogue-parser` | 0.667 | 1.0 | +0.333 | Clearest SkillOS gain |
| `sales-pivot-analysis` | 0.0 | 0.0 | 0.0 | Data processing worked partly; pivot metadata failed |
| `software-dependency-audit` | 0.0 | 0.0 | 0.0 | CSV structure worked partly; exact ground truth failed |

## 4. Task 1: citation-check

The no-skill baseline already passed this task with reward `1.0`, and SkillOS generated skills also passed with reward `1.0`. This is a no-regression result rather than a clear improvement.

The useful lesson is that generated Skills did not break an already solvable task. However, the SkillOS run used more tool calls than the no-skill run, so this task also warns us that injecting too much Skill context can make an easy task heavier.

Paper-method interpretation:

- SkillsBench is the key method here because the verifier lets us separate correctness from efficiency.
- SkillX-style layering and Ctx2Skill-lite context extraction did not visibly improve the score because the baseline already solved the task.

Next improvement:

- Use sharper Skill routing so easy tasks receive only a short checklist or the top few relevant Skills.
- Track tool calls, elapsed time, and token use as first-class metrics.

## 5. Task 2: court-form-filling

The no-skill baseline passed with reward `1.0`, and SkillOS generated skills also passed with reward `1.0`. The generated-skill run used a similar number of tool calls.

This is again a no-regression result. It shows that SkillOS can preserve correct behavior on a form-like task, but it does not prove that the system has mastered all real browser or PDF form-filling cases.

Paper-method interpretation:

- WebXSkill/SkillWeaver-like ideas are relevant because the task resembles a multi-step workflow that can be represented as a reusable Skill.
- Ctx2Skill-lite is useful for extracting field and output constraints from instructions.
- SKILLFOUNDRY-style lifecycle thinking matters because a passed task can become validated evidence for a Skill.

Next improvement:

- Add a field-mapping verifier for form Skills.
- Add DOM or screenshot evidence when the form is a real browser/PDF workflow.
- Use Version Lab to show how a form Skill changes when fields or postconditions change.

## 6. Task 3: dialogue-parser

This is the strongest positive result. The no-skill baseline reached `0.667`, while SkillOS generated skills reached `1.0`.

The reason is straightforward. The task asks the agent to turn a dialogue script into a structured graph. SkillOS is currently good at exactly this kind of work: extracting structure, constraints, relationships, and validation conditions from source material.

The generated Skills helped in three ways:

- The document-derived Skill preserved the task constraints: nodes, edges, reachability, branches, and terminal nodes.
- The `past_skills` path normalized an existing dialogue-graph-like skill into the SkillOS schema.
- The Ctx2Skill-lite evidence made the Skill more than a summary; it carried task expectations and failure signals.

Paper-method interpretation:

- SkillX worked well here because the dialogue graph ability is naturally a functional Skill rather than a one-line note.
- Ctx2Skill worked well here because the context contained explicit structural rules that could be turned into task evidence.
- HIN/GraphRAG-style graph thinking helped because the output itself is graph-shaped.
- SkillsBench confirmed the improvement with an external verifier.

What is still missing:

- The current Skill is still mostly guidance for an agent, not a fully reusable parser library.
- More varied dialogue formats should be tested before claiming broad generality.

Next improvement:

- Turn the dialogue Skill into an executable parser template.
- Add graph consistency checks: every edge target exists, every node is reachable, and terminal nodes are reachable.
- Use failure cases to create a MaintenanceProposal and then a Version Lab repair example.

This task should be the main positive demo case for “SkillOS generated skills can really help.”

## 7. Task 4: sales-pivot-analysis

This task failed with reward `0.0`, but the failure is not a total failure. The verifier shows that many data-level checks passed:

- required source columns existed;
- row count was reasonable;
- `Quarter` values were valid;
- `STATE` values were valid;
- `Total = Earners * Median income`;
- SA2 codes came from the expected source.

The real failure was Excel pivot metadata:

- row fields resolved to `None` instead of `STATE`;
- the `State Income Quartile` column field resolved to `None` instead of `Quarter`;
- the pivot cache had no field definitions.

In plain language, the agent made a workbook with much of the right data, but it did not make a verifier-compatible Excel pivot table.

Paper-method interpretation:

- Ctx2Skill-lite partially worked. The generated xlsx/document Skills captured terms like `cacheId=0`, field indices, and pivot table requirements.
- SkillX partially worked. The system correctly treated this as a functional spreadsheet Skill.
- SkillsBench was essential because it revealed that the workbook only looked plausible; the underlying pivot metadata was wrong.

Where the paper-inspired method was not enough:

- Ctx2Skill-lite did not run a strong enough challenge/replay loop to catch the missing `cacheFields`.
- SkillX layering stopped at the functional level. We still need an atomic implementation Skill for constructing pivot cache XML/metadata.
- SKILLFOUNDRY-style validation is not complete here because this xlsx Skill is not yet a validated Skill.

Next improvement:

- Build an atomic Skill such as `create_openpyxl_pivot_cache_with_fields`.
- Add a local harness preflight that opens the workbook and checks `_pivots[0]`, `cacheFields`, `rowFields`, and `colFields`.
- Use Version Lab to show a failed xlsx Skill being revised into a new version and reverified.

This is a good next repair-loop target because the verifier already tells us exactly what to fix.

## 8. Task 5: software-dependency-audit

This task also failed with reward `0.0`, but it failed cleanly. The final single-task rerun had no agent error and no verifier error.

The verifier ran 7 checks. Six passed. The failing check was exact ground-truth CSV matching.

That means the agent was not completely wrong. It produced a CSV, filled required fields, used valid severity formatting, and found the general vulnerability-report shape. The failure was in exact normalization:

- reference URL choice;
- fixed-version formatting;
- title text.

In plain language, the agent knew it needed to submit a vulnerability table, but it did not submit the exact vulnerability table expected by the benchmark.

Paper-method interpretation:

- Ctx2Skill-lite helped extract the required fields: Package, Version, CVE ID, Severity, CVSS score, Fixed version, Title, and URL.
- Tool/API/document extraction ideas helped represent Trivy/offline scan and CSV reporting as Skills.
- SkillsBench was essential because it caught a subtle exact-match failure.

Where the paper-inspired method was not enough:

- Ctx2Skill-lite did not generate strong enough negative challenges, such as “which URL source wins when multiple CVE URLs exist?” or “how should multiple fixed versions be formatted?”
- Reflexion, ExpeL, and SkillClaw point to failure-driven repair, but we have not yet automated that loop.
- Tool-use papers help with tool understanding, but they do not automatically solve benchmark-specific output normalization.

Next improvement:

- Build an atomic Skill such as `vulnerability_csv_exact_normalizer`.
- Encode URL priority rules, fixed-version formatting, title source, and CSV field order.
- Convert verifier mismatch into a MaintenanceProposal.
- Create a repaired version in Version Lab and rerun the task.

This is the best negative demo case for “benchmark failure can drive Skill evolution.”

## 9. Method-Level Summary

| Method source | What worked | What did not work yet |
| --- | --- | --- |
| SkillX | Useful for layered Skill representation and `past_skills` normalization | Needs finer atomic implementation Skills for exact artifacts |
| Ctx2Skill | Useful for document-to-skill evidence and structural constraints | Lite version lacks strong self-play/replay for hidden low-level details |
| SkillsBench | Gives objective verifier results and exposes real failure modes | Current run covers a small P0 subset, not the full benchmark |
| SKILLFOUNDRY | Supports provenance, lifecycle, validation, and version framing | Failed Skills are not yet automatically repaired into validated Skills |
| Reflexion / ExpeL / SkillClaw | Provide the right direction for repair loops | Repair loop is not fully automated yet |
| HIN / GraphRAG | Helpful for graph-based explanation and provenance display | Graph quality is not yet independently benchmarked |
| Toolformer / Gorilla / ToolLLM / API-Bank | Helpful for API/doc/script skill extraction | Exact tool-output normalization still needs local verifiers |

## 10. What To Do Next

The next step should not be to blindly run more benchmark tasks. The better next step is to fix the two clear failures and show that SkillOS can improve its own Skills.

For `sales-pivot-analysis`, the repair target is low-level Excel pivot metadata. The system needs an xlsx Skill with executable code and a local verifier.

For `software-dependency-audit`, the repair target is exact CSV normalization. The system needs a security reporting Skill that encodes source priority and exact field formatting.

For successful tasks such as `dialogue-parser`, the next goal is efficiency and robustness: fewer irrelevant injected Skills, shorter guidance, executable templates, and more input-format coverage.

## 11. Recommended Demo Wording

Do not say:

```text
SkillOS fully improves SkillsBench.
```

Say:

```text
We connected SkillOS-generated skills to a real SkillsBench Docker-sandbox subset. On this 5-task P0 subset, SkillOS generated skills improved the pass count from 2/5 to 3/5. The strongest gain is dialogue-parser, where structural skill extraction helped the agent reach full reward. The two failures are also informative: one requires exact Excel pivot metadata, and the other requires exact security CSV normalization. These are now concrete targets for the next Skill repair loop.
```

Chinese explanation for group meeting:

```text
这次最重要的不是分数本身，而是我们终于把 SkillOS 放进外部 benchmark 里看它到底有没有帮助。结果是：它确实帮到了 dialogue-parser，从 0.667 到 1.0；但对 Excel pivot 和安全审计这种要求底层输出完全精确的任务还不够。这说明主线是对的，但下一步要把说明型 Skill 升级成可执行、可验证、可修复的 Skill。
```

## 12. Final Judgment

The current result is strong enough for a PR and group demo because it shows a complete system path: runnable service, five-input Skill generation, graph/version management, local harness verification, and external benchmark evidence.

It is not yet a final paper-level system. The next paper-strengthening step is to demonstrate failure-driven Skill evolution: take `sales-pivot-analysis` and `software-dependency-audit`, repair the generated Skills, verify the new versions, and show whether score, tool calls, and time improve.
