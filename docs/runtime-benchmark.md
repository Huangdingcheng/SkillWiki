# Runtime Benchmark Scoring

This benchmark evaluates whether the SkillOS runtime can retrieve, plan, execute, and verify a fixed set of standard tasks with a real LLM attached.

## How to run

```bash
cd skillos
python -m skillos.cli benchmark-runtime --api-key "YOUR_DEEPSEEK_API_KEY"
```

API key location: replace `YOUR_DEEPSEEK_API_KEY` with your own DeepSeek API key. Do not write the key into repository files.

## Task set

The benchmark now uses six standard tasks:

| Task ID | Goal | Expected Skill path |
| --- | --- | --- |
| `web_form_login` | Fill email/password and submit a login form. | `fill_form` |
| `web_click_button` | Find a checkout button and click it. | `locate_element -> click_element` |
| `text_summary` | Summarize a long product review. | `summarize_text` |
| `api_post_json` | Send JSON to an HTTP API and report status. | `post_json_api` |
| `support_start_check_flow` | Prepare customer data, process an order, and validate the processed order. | `prepare_customer_data -> process_order -> validate_order` |
| `missing_skill_recovery_route` | Request an unavailable payment capture skill and report the missing capability. | no executable skill; expected recovery route |

The Skills are seeded in memory by `skillos.evals.runtime_benchmark.benchmark_skills()`. They use deterministic code implementations so the benchmark measures runtime agent decisions rather than external tools.

## Score formula

Each task receives a score from 0 to 100. Runtime Benchmark v2 measures the full Member C runtime architecture, not only task success:

```text
total =
  skill_group * 0.15 +
  planning * 0.15 +
  composition * 0.20 +
  execution * 0.25 +
  verification * 0.10 +
  recovery * 0.10 +
  memory * 0.05
```

The final benchmark score is the average of all task totals.

## Dimensions

`retrieval_score` measures whether Retriever selected the expected Skill names. It is exact set coverage:

```text
matched expected skills / expected skills
```

`skill_group_score` measures whether the structured runtime SkillGroup is correct:

```text
skill_group =
  start_role_accuracy * 0.35 +
  support_role_accuracy * 0.25 +
  check_role_accuracy * 0.25 +
  avoid_role_accuracy * 0.15
```

This dimension evaluates the Group-of-Skills style contract:

- `start_skill_ids`: main entry skills
- `support_skill_ids`: preparation or helper skills
- `check_skill_ids`: validation or postcondition skills
- `avoid_skill_ids`: unsuitable or risky candidates

`planning_score` measures whether Planner used the expected Skills, kept the plan short, and preserved expected order:

```text
planning = coverage * 0.65 + brevity * 0.25 + order * 0.10
```

`composition_score` measures whether CompositionAgent builds a valid executable Skill DAG:

```text
composition =
  expected_edge_coverage * 0.55 +
  dag_validity * 0.35 +
  parallel_group_available * 0.10
```

The benchmark checks for valid nodes, no self-loops, no duplicate edges, no cycles, and expected edges such as:

```text
prepare_customer_data -> process_order
process_order -> validate_order
```

`execution_score` measures whether planned steps succeeded and whether required output keys appeared in final state:

```text
execution = step success ratio * 0.70 + required output key coverage * 0.30
```

`verification_score` uses a hybrid policy. The LLM Verifier score is used when it is strong. A rule-based floor is applied when the execution succeeded and required output keys are present:

```text
verification = max(llm_verifier_score, rule_based_floor)
```

The default rule-based floor is `0.70`. This prevents the benchmark from heavily penalizing deterministic simulated outputs when the LLM Verifier is overly strict or lacks UI/API context.

`recovery_score` measures Skill-RAG style failure-state awareness:

```text
recovery =
  failure_type_accuracy * 0.60 +
  recovery_route_accuracy * 0.40
```

For example, `missing_skill_recovery_route` expects:

```text
failure_type = missing_skill
recovery_route = retrieve_alternative_skill
```

`memory_score` measures whether task-local runtime memory records useful execution evidence:

```text
memory =
  selected_skill_recorded * 0.35 +
  step_io_recorded * 0.35 +
  lifecycle_event_recorded * 0.30
```

Runtime memory is exposed through `SkillExecutor.last_runtime_memory`. It is not injected into `ExecutionResult.final_state`, so the public execution API remains compatible.

## Output fields

- `Score`: final average score, 0 to 100.
- `Avg latency`: average wall-clock task latency.
- `LLM tokens observed`: token count reported by the provider, when available.
- `status`: runtime execution status, one of `success`, `partial`, or `failed`.
- `notes`: non-fatal issues such as LLM verifier disagreement or retrieval requesting generation.
- Per-task sub-scores: `retrieval`, `skill_group`, `planning`, `composition`, `execution`, `verification`, `recovery`, and `memory`.

## Interpreting results

A high score with high latency means the architecture is correct but the LLM/API path is slow. Increase timeout or reduce prompt size before changing runtime logic.

A high retrieval score but low `skill_group_score` means the right candidates were found but role assignment is weak.

A high retrieval score but low planning score means candidates are found but the planner prompt or normalization needs work.

A high planning score but low composition score means the plan used the right skills but the DAG is invalid, missing expected edges, or not exposing useful dependency layers.

A high execution score but low verification score usually means the Verifier prompt or expected evidence is too strict. The rule-based floor reduces noise, but repeated low LLM verification should still be reviewed.

A low retrieval score means search ranking, Retriever prompt, or the seeded task-to-skill descriptions need improvement.

A low recovery score means Verifier/Reflection is misclassifying failure states or selecting the wrong recovery route.

A low memory score means runtime execution evidence is not being recorded well enough for later debugging, repair, or planning reuse.

The `missing_skill_recovery_route` task is intentionally expected to fail execution. A low total score for this case is acceptable if `recovery_score` is high, because the goal is to verify that the runtime recognizes the missing capability and routes it correctly.

## Local validation commands

Use the backend virtual environment from the repository root:

```powershell
cd D:\SKILL\project_1\skillos-demo-handoff-20260510
.\skillos\venv\Scripts\pytest.exe skillos\tests\test_runtime_benchmark.py -q
.\skillos\venv\Scripts\pytest.exe skillos\tests\test_skill_runtime_phase2.py skillos\tests\test_skill_runtime_phase3.py skillos\tests\test_skill_runtime_phase4.py skillos\tests\test_skill_runtime_memory.py -q
```

Current deterministic benchmark result with the fake LLM test path:

```text
Score: 91.17/100
Cases: 6
Avg latency: 1.78 ms
LLM tokens observed: 180
```
