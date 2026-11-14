# Reproduce the SkillWiki paper results

This package independently re-scores **all 4,572 archived task answers**, retaining both original model answers and system-final outputs, and reconstructs the paper's two result tables. It also includes all **18 model proposals**, original failed attempts, later revisions, qualification, governance, release and recovery records. Python **3.10+**, standard library only; no credentials or model calls are needed for the offline commands.

## Quick start

From the repository root:

~~~bash
python public-experiments/verify.py
python public-experiments/reproduction/rescore.py
python public-experiments/reproduction/reproduce_tables.py
~~~

The second command executes the archived scoring code on the actual raw and final answers, rather than copying summary scores. It checks all 4,572 answers against their recorded grades. The third derives the reported usage scores, 12 event-level target/retention results, all 85 qualification batches, selected revision/recovery totals, eight supplementary batches, and all 120 effective governance positions. Each command fails on a discrepancy.

To save newly derived files, choose a **new** output directory:

~~~bash
python public-experiments/reproduction/rescore.py --output reproduced-scores
python public-experiments/reproduction/reproduce_tables.py --output reproduced-tables.json
python public-experiments/reproduction/extract.py extracted-evidence
~~~

Extraction exposes sanitized JSON, source, candidate, proof, frozen implementation, contract and historical runner files at their logical archive paths. The extraction map records both original source digests and the digests of exported bytes. Machine paths are replaced with archive URIs, provider identifiers are hashed, and credential fields are redacted. Synthetic task credentials remain fixtures. Historical Python drivers are source references, not commands to run unmodified: they use the original workspace layout and consumed execution guards.

## What is covered

| Evidence | Coverage and interpretation |
|---|---|
| Model calls | Exactly 4,590 admitted calls: 4,572 task answers and 18 proposals; request indices match the public call ledger |
| Skill use | 60 inputs, three conditions, two technical repeats: 360 original and final answers |
| Core source-update study | 12 events × 16 target/retention inputs × three conditions × two repeats: 1,152 answers |
| Qualification | 85 batches × 36 cases: 3,060 answers, including unsuccessful and superseded batches |
| Follow-up revisions | All five first revisions, the failed U06 first revision and its second revision, subsequent recovery runs, U08 follow-up, and governance supplements |
| Governance | Original 120 positions, blocked positions retained; 100 original executions plus 20 subsequent executions make 120 effective positions, not one same-version matrix |
| Proposals | Full request, original proposal text, provider response and available parsing/binding/validation records; task-answer final scores do not apply to proposals |
| System evidence | Native outputs, input/identity/source bindings, state records, release/recovery proofs, and frozen source files; see the per-asset archive entries |

The five phases contain 360, 3,540, 221, 180 and 289 calls. The manifest and per-call phase identity preserve the distinction between the original study and later development revisions. All 85 qualification batches are available even though the paper highlights only selected rows. They include the failed U06 R1 batch (raw 30/36, final 35/36), selected four-R1-plus-one-R2 candidates (raw 171/180, final 180/180), and recovery (raw 167/180, final 180/180). Nothing is backfilled into an earlier version's results.

The unresolved U03 classification case and original U08 failure remain in the exported records. U08's later 36/36 update and recovery results have their own identities. Recorded blocked/unrun positions remain distinguishable from executions.

## Model and runtime identity

The recorded API model is `deepseek-flash`, temperature 0, thinking disabled, zero automatic retries, with task/proposal limits of 2,048/4,096 tokens. The paper display name **DeepSeek-V4.1-Flash-1017** follows the provider's [17 October 2026 announcement](https://api-docs.deepseek.com/updates/). Original runs were on 7–9 November 2026. The alias is mutable; record the actual date, returned model and fingerprint of any new run. A new API call need not reproduce historical bytes.

Each batch retains its own candidate, source and frozen implementation identities. The Git commit publishing this package identifies the export, not a claim that the historical experiment used the repository's latest product.

## Optional new model requests

Dry-run the selection first. This does not need credentials:

~~~bash
python public-experiments/reproduction/replay_requests.py --call-index 1
python public-experiments/reproduction/replay_requests.py --call-index 1 --call-index 2 --max-calls 2
~~~

To execute selected **paid** requests, set `DEEPSEEK_API_KEY` in your environment and use a new output directory:

~~~bash
python public-experiments/reproduction/replay_requests.py --call-index 1 --max-calls 1 --execute --output new-model-run
~~~

The replay entrypoint uses the archived messages and generation settings, performs no automatic retries, stops on transport failure, and saves a new response identity. It does not overwrite historical evidence. The dry-run and argument gates were tested; no new paid requests were made when preparing this package.

This entrypoint reproduces **model request execution**, not the product's native final-output processing or stateful release/recovery operations. For the latter, inspect the extracted frozen implementation, historical drivers and state/proof evidence, and follow [USER_GUIDE.md](../../USER_GUIDE.md) for current product operations. A turnkey portable rerun of every historical product state transition is not claimed. The offline scorer independently reconstructs numerical results from the archived final outputs.

## What “revision” means

Follow-ups were development revisions driven by observed failures. Engineers added source-scope separation and general checking guidance; model proposals were then bound into new skill identities, qualified, authorized for release and revalidated after recovery. The general U06 R2 guidance was written by engineers. These results demonstrate the revised workflow and candidates; they are not evidence that the model autonomously modified the product or fixed every failure.

## Files and verification

- `manifest.json`: counts, phase identities and compressed-file hashes.
- `calls/phase-*.jsonl.gz`: one full record per admitted call, including request, raw answer, final output where applicable, grades and evidence links.
- `assets/assets-*.jsonl.gz`: sanitized historical materials with logical paths and original digests.
- `scorers/`: exact original grading code and source layout; Q1 URL/response contract and later expansion oracle remain separate.
- `rescore.py`, `reproduce_tables.py`: portable offline score and table reconstruction.
- `extract.py`: safe evidence extraction.
- `replay_requests.py`: bounded optional new model requests.
- `validation.json`: preparation-time checks and precise scope, including no new paid experiment execution.

See [中文说明](README.zh-CN.md) and the [original companion](../README.md) for the task materials and historical summary tables.
