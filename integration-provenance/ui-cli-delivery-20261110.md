# CLI and frontend integration delivery

Date: 2026-11-10. Delivery branch: `codex/skillwiki-demo-product-20261109`.
Owner review: [PR #16](https://github.com/Huangdingcheng/SkillWiki/pull/16).

The branch contains the isolated product, native CLI workflow, persisted graph
projection, frontend identity/lifecycle adapters and Git read-performance fix.
The documentation follow-up preserves the production behavior and UI styling.
It adds English/Chinese user guides, native request examples and frontend setup
instructions, and corrects stale startup ports, proxy targets, Node requirements
and export-checkpoint notes.

## Fresh checks for this documentation delivery

| Check | Result |
|---|---|
| CLI contracts against real isolated FastAPI routes | 13 passed, exit 0; model transport blocked |
| Frontend lint | Exit 0 after the test correction |
| Frontend TypeScript/production build | Exit 0; existing chunk-size advisory |
| Actual graph component adapter checks | 13 passed, 0 failed |
| Native release request adapter | Passed; JSON request body verified |
| State/evidence/recovery checks | Passed, including deferred saved-run loading, failed transport and stale response after cleanup |
| Guide request examples | Candidate, test-case array, version and snapshot validated against actual Pydantic models |
| Documented CLI command help | 35 command paths returned exit 0 |
| Relative document links | 29 targets existed across the eight checked guides |
| Paid model requests | 0 |

The state script initially failed its historical blanket ban on `setTimeout`.
The current component uses a zero-delay callback to read saved backend records.
The corrected test executes that actual effect and checks pending, failure and
stale-response behavior rather than prohibiting the scheduling mechanism. No
production component was edited for this correction. Earlier failed checks
remain part of the local audit; they are not claimed to have passed.

## Previously checked integration behavior

The finite browser acceptance covered 11 existing routes and focused release,
recovery, canonical Skill navigation and graph interactions on their recorded
integration revisions. The later `ec7471b1` storage change passed 61 storage/graph
regressions and was checked in the live browser with 31 Skill nodes and 7 edges.
These checks do not rerun the paper experiment matrix on the latest commit.

## Scope and review boundaries

Only product source, tests, public documentation and authored request examples
are included. Local configuration, API keys, runtime stores, logs, build outputs,
research reports and experiment receipts are excluded from this delivery.
The original experiment-export manifests are retained without rewriting hashes.

The web stored-evidence release action and CLI evidence-bundle submission use
different native requests; the guides describe both. Dependency maintenance
remains separate: the earlier audit recorded 27 findings, including 15 high.
The complete paper reproduction package is still a separate deliverable. This
PR is for owner review and does not itself merge or tag a public release.
