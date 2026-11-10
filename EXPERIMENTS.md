# SkillWiki evaluation

SkillWiki connects reusable skill assets with their sources, verification evidence,
versions and release decisions. The evaluation examines skill utility, evidence-based
governance, and adaptation to source changes.

## Materials and methods

The Petstore/OpenAPI tasks cover creation, lookup and deletion, including request
construction, authentication, field constraints and supplied-response interpretation.
The evaluation uses 60 task inputs, 60 governance states in 30 pairs, and 12 related
source-change events with eight target and eight retention inputs each. Changes
cover routes, required request fields and enumeration values.

Model-backed execution uses `deepseek-flash`, temperature 0, output limits of 2,048
tokens for tasks and 4,096 for proposals, and zero automatic retries. The main
harness is `local_skillwiki`. One model proposal per update attempt is checked for
source identity and modification scope before native version creation. Native
verification, authorized release, recovery and persistent readback follow. Tasks
evaluate constructed requests and supplied responses without calling the remote
business API. The archived evaluation contains 4,590 unique model calls: 4,572 task
answers and 18 update proposals. Repeated answers are observations within the
specified input sets, rather than independent task families.

## Skill utility

Each of the 60 inputs is evaluated under three conditions with two technical repeats.

| Condition | Completed and well formed | Raw model correct | Accuracy |
|---|---:|---:|---:|
| No domain material | 120/120 | 84/120 | 70.00% |
| Source material | 120/120 | 112/120 | 93.33% |
| Fixed skill representation | 120/120 | 120/120 | 100.00% |

Skills improve accuracy by 6.67 percentage points over source material and 30 points
over no material. Each operation achieves 40/40 with skills. Raw and final answers
coincide here, showing the utility of the selected skill representations without
programmatic answer correction. This comparison evaluates fixed research skills.

## Governance

The six themes cover valid release, identity/content binding, applicability,
completeness, deferral/resolution, and recovery/state consistency. An independently
implemented Git/CI+ baseline applies the same evidence contract and performs its own
repository actions without reading SkillWiki decisions.

| Evaluation set | SkillWiki correct | Git/CI+ correct | State inconsistencies | False accepts |
|---|---:|---:|---:|---:|
| Main executed states | 50/50 | 50/50 | 0 on both routes | 0 on both routes |
| Remaining states, supplementary revision | 10/10 | 10/10 | 0 on both routes | 0 on both routes |

SkillWiki's native decisions agree with a carefully implemented Git/CI+ workflow
on the evaluated evidence requirements. These capabilities are integrated with
skill identities, provenance, verification and lifecycle state in one interface.
Git/CI+ includes explicit evidence rules beyond basic Git versioning. One main-set
SkillWiki input-validation error belongs to its preregistered allowed decision set;
its original classification remains in the data. Coverage spans separate frozen
revisions, with main and supplementary results reported separately.

## Source adaptation and lifecycle

Each event compares an original-rule reference, an unchanged skill, and an updated
skill, with two repeats per input: 1,152 core answers in total.

| Main evaluation metric | Unchanged skill | Updated: raw model | Updated: final system |
|---|---:|---:|---:|
| Target behavior under new rules | 0/192 | 176/192 | 192/192 |
| Retained behavior | 192/192 | 173/192 | 173/192 |

Updates satisfy all target observations; explicit source checks correct 16 raw
answers. Seven of 12 events also meet the event criterion of target improvement
without retention regression. Full qualification checks 36 cases per identity;
five updated events on each route qualify and complete release and revalidated
recovery in the main evaluation.

With request and response constraints separated in subsequent revisions, the five
revised candidates achieve 180/180 final qualification answers (171/180 raw). Their
five recovered identities achieve 180/180 final answers (167/180 raw), followed by
release and persistent readback. The candidate set comprises four R1 identities
and one R2 identity; U06 R1 remains recorded as 35/36. Supplementary U08 updated
and recovered identities each achieve 36/36 in both raw and final answers.

The records demonstrate source adaptation coupled to evidence-gated release and
recovery. Eleven events have successful lifecycle chains at their applicable
recorded revisions. U03 remains unqualified at 35/36: an out-of-range integer is
rejected, but its error category differs from the contract. These results retain
their revision identities rather than forming a single-version aggregate.

## Artifacts

The [public companion](public-experiments/README.md) includes synthetic inputs,
expected answers, source snapshots, state definitions, result tables and a
de-identified call index. The [Chinese account](EXPERIMENTS.zh-CN.md) and
[LaTeX evaluation section](public-experiments/paper/evaluation.tex) use the same
counts. Materials are development/regression and related extensions within one
API family. Current product usage is documented in the [user guide](USER_GUIDE.md).
