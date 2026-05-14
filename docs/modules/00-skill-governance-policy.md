# Skill Governance Policy v0.1

This policy defines the local, Git-governed lifecycle for SkillOS demo-paper
evidence. It is based on the paper-driven B backlog and keeps P0 deliberately
human-in-the-loop: agents may propose changes, but accepted Skills are versioned
through reviewable snapshots, diffs, and release tags.

## Scope

Governance covers durable Skill assets:

- `Skill` metadata, interface, implementation, evaluation contract, provenance,
  and dependency references.
- Git snapshots under `skills/<skill_id>/<version>.json`.
- Structured diffs, review bundles, release tags, and restore commits.

Governance does not snapshot runtime noise:

- Per-run latency counters, success/failure counters, and timestamps are
  excluded from Skill snapshots.
- Runtime metrics can trigger maintenance, but the snapshot should only include
  stable validation summaries or verifier references through `evaluation`.

## Roles

| Role | Responsibility |
| --- | --- |
| Runtime / C | Runs Skills, records execution results, and produces verifier evidence. |
| Self-management / D | Converts failures or drift into `MaintenanceProposal` evidence. |
| Governance / B | Creates snapshots, structured diffs, review bundles, release tags, and restore commits. |
| Repository / A | Persists accepted Skill versions and exposes provenance and graph links. |
| Frontend / E | Lets a human inspect diffs, accept or reject proposals, release versions, and demonstrate rollback. |
| Human reviewer | Makes the final accept/reject decision for P0 Skill changes. |

## Required Governance Events

A Skill change must go through governance when it changes any durable behavior:

- Interface schema, preconditions, postconditions, or side effects.
- Prompt template, code, tool calls, or sub-skill composition.
- Provenance, parent Skill links, source references, or dependency lists.
- Evaluation verifier specs, benchmark task ids, or validation summary.
- Lifecycle release, rollback, deprecation, merge, split, or repair.

Simple runtime observations do not require a snapshot until they become a
validated summary, maintenance proposal, or accepted Skill patch.

## P0 Workflow

```text
failure or improvement signal
-> reflection / MaintenanceProposal
-> human chooses to review
-> B creates review bundle
-> structured diff and breaking flag are inspected
-> accepted patch is committed as a Skill snapshot
-> release tag is created after validation
-> A/E can show graph Version and provenance evidence
-> rollback uses a restore commit when needed
```

The live Skill must not be silently overwritten by an agent-generated proposal.
For P0, a proposal creates evidence for review first. Accepted changes can then
be saved back to the canonical repository and released.

## Snapshot Rules

Snapshots are stable JSON files written to:

```text
skills/<skill_id>/<version>.json
```

Included fields:

- Identity and classification: `skill_id`, `name`, `version`, `skill_type`,
  `domain`, `state`, `tags`.
- Contract: `interface`, `implementation`, `test_cases`, `evaluation`.
- Governance context: `provenance`, `dependency_ids`, `component_ids`.

Excluded fields:

- `metrics`
- `created_at`, `updated_at`, `released_at`, `deprecated_at`
- transient execution history

## Structured Diff Rules

Structured diffs classify changes for the Version page and review workflow:

| Category | Examples |
| --- | --- |
| `schema_change` | input/output JSON schema property or required-field change |
| `postcondition_change` | changed natural-language postcondition guarantees |
| `implementation_change` | prompt, code, tool call, or executable representation change |
| `dependency_change` | sub-skill, component, or dependency reference change |
| `provenance_change` | changed source, parent Skill, or creation evidence |
| `metadata_change` | description, state, tags, domain, or display-only fields |

Breaking changes require stricter review:

- removing an input property
- adding a required input field
- adding, removing, or changing output schema properties
- removing prompt/code implementation content
- removing a sub-skill from an executable composition

Non-breaking changes still require review when they affect a released Skill, but
they can usually be released as a patch version.

## Review Recommendations

`SnapshotDiffResponse.review_recommendation` uses this rule:

| Value | Meaning |
| --- | --- |
| `no_changes` | No durable Skill diff was detected. |
| `review_required` | A non-breaking change needs human review before release. |
| `breaking_review_required` | The change may break callers or dependent Skills. |

## Rollback Rule

Rollback must use a restore commit, not a destructive reset. The restore commit
message records the source ref:

```text
skill(<name>): restore from <ref>
```

This preserves the evidence chain for the demo paper and for later graph
visualization.

## Demo-Paper Evidence

For a minimal B-module demo, the team should be able to show:

- a Skill snapshot commit
- a structured diff with category and breaking flag
- a review recommendation
- a release tag
- a restore commit rollback
- a later graph path linking `Skill -> Version -> Validation`
