# Evaluation companion

Read the [results](../EXPERIMENTS.md) or [中文结果](../EXPERIMENTS.zh-CN.md).
This package contains public task materials and result tables for the completed
evaluation. It is an offline companion; verification does not make model calls.

For independent re-scoring of every original and final answer, reconstruction of
the paper tables, revision-specific evidence and optional new model requests, see
the [reproducibility README](reproduction/README.md) or [中文说明](reproduction/README.zh-CN.md).
The original consistency check below remains available.

| Material | Contents |
|---|---|
| `materials/use-inputs.json` | 60 synthetic use cases and expected outputs; original retained/extended partitions |
| `materials/update-inputs.json` | 192 event/input combinations with model inputs and old/new expected outputs |
| `materials/sources/` | Three original and 12 changed OpenAPI operation snapshots |
| `materials/governance-states.json` | 60 paired state definitions and allowed decisions |
| `data/original-12-core-events.csv` | Target, retention and event-level main results |
| `data/all-12-events.csv` | Full qualification and lifecycle status across all 12 events, separated by revision |
| `data/original-120-governance.csv` | All original governance positions, including blocked positions and original classifications |
| `data/all-120-tracking.csv` | Original and supplementary coverage by state and route |
| `data/new-qualification-batches.csv` | Eight supplementary batches, raw and final scores |
| `data/recovery-5-events.csv` | Five recovered identities: revalidation, old-proof rejection and release |
| `data/call-index.csv` | 4,590 indexed calls, task/proposal type, hashed request identity and original receipt digest |
| `provenance.json` | Source digests and experiment/product identity boundary |
| `paper/` | English and Chinese LaTeX evaluation sections for manuscript integration |

Run from the repository root with Python 3.10+:

```bash
python public-experiments/verify.py
```

The check verifies packaged file hashes, counts, cross-table totals, coverage and
the recorded unresolved event. It checks the exported records; it does not
re-execute or independently re-score model answers. No dependency installation or
credentials are required.

## Source and identity

The source snapshots identify Swagger Petstore, OpenAPI 3.0, and Apache 2.0 in
their embedded metadata. Attribution and original source links remain in each
snapshot. Modified snapshots define the documented experimental source changes.
Authentication values beginning `SIMULATED_` are synthetic fixtures, not credentials.

Q1 is the retained 2026-11-07 run with fixed comparison carriers and the
`api-url-representation-v3-20261031` scoring contract. Main governance/update
results belong to the 2026-11-08 frozen product and candidates; subsequent
qualification/recovery results retain their own R1/R2/supplement identities.
The last experiment product manifest digest is recorded in `data/summary.json`.
The current product integration descends from that source export and includes
later CLI/frontend work. Its Git revision does not reassign historical scores.

The original companion supports inspection of materials and reported counts.
The added reproducibility directory provides all 4,590 admitted model calls,
complete requests and original answers, system-final outputs for 4,572 tasks,
scorers, candidates, frozen source and revision-specific proof/state evidence.
Its offline entrypoints independently re-score the answers and reconstruct the
reported tables. Optional request replay produces new raw model answers; it does
not replay every native product state transition. Current product operations can
be exercised using the [user guide](../USER_GUIDE.md).

## Manuscript use

The `.tex` files are section fragments, not standalone documents. Include the
English fragment with `\input{public-experiments/paper/evaluation.tex}` from the
repository root, or copy it into the manuscript's section tree. The Chinese
fragment requires a Chinese-capable manuscript configuration. The section/table
labels are prefixed `sw-eval-`; no additional LaTeX packages are required by the
English fragment. No claim of a compiled final paper is made by this companion.
