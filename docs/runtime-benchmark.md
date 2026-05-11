# Runtime Benchmark Scoring

This benchmark evaluates whether the SkillOS runtime can retrieve, plan, execute, and verify a fixed set of standard tasks with a real LLM attached.

## How to run

```bash
cd skillos
python -m skillos.cli benchmark-runtime --api-key "YOUR_DEEPSEEK_API_KEY"
```

API key location: replace `YOUR_DEEPSEEK_API_KEY` with your own DeepSeek API key. Do not write the key into repository files.

## Task set

The current benchmark uses four standard tasks:

| Task ID | Goal | Expected Skill path |
| --- | --- | --- |
| `web_form_login` | Fill email/password and submit a login form. | `fill_form` |
| `web_click_button` | Find a checkout button and click it. | `locate_element -> click_element` |
| `text_summary` | Summarize a long product review. | `summarize_text` |
| `api_post_json` | Send JSON to an HTTP API and report status. | `post_json_api` |

The Skills are seeded in memory by `skillos.evals.runtime_benchmark.benchmark_skills()`. They use deterministic code implementations so the benchmark measures runtime agent decisions rather than external tools.

## Score formula

Each task receives a score from 0 to 100:

```text
total = retrieval * 0.30 + planning * 0.25 + execution * 0.30 + verification * 0.15
```

The final benchmark score is the average of all task totals.

## Dimensions

`retrieval_score` measures whether Retriever selected the expected Skill names. It is exact set coverage:

```text
matched expected skills / expected skills
```

`planning_score` measures whether Planner used the expected Skills, kept the plan short, and preserved expected order:

```text
planning = coverage * 0.65 + brevity * 0.25 + order * 0.10
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

## Output fields

- `Score`: final average score, 0 to 100.
- `Avg latency`: average wall-clock task latency.
- `LLM tokens observed`: token count reported by the provider, when available.
- `status`: runtime execution status, one of `success`, `partial`, or `failed`.
- `notes`: non-fatal issues such as LLM verifier disagreement or retrieval requesting generation.

## Interpreting results

A high score with high latency means the architecture is correct but the LLM/API path is slow. Increase timeout or reduce prompt size before changing runtime logic.

A high retrieval score but low planning score means candidates are found but the planner prompt or normalization needs work.

A high execution score but low verification score usually means the Verifier prompt or expected evidence is too strict. The rule-based floor reduces noise, but repeated low LLM verification should still be reviewed.

A low retrieval score means search ranking, Retriever prompt, or the seeded task-to-skill descriptions need improvement.
