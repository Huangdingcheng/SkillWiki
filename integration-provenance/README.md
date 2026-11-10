# Local product source candidate — 2026-11-09

This branch isolates the actual current product source from the shared research
workspace. It is not a new experimental result or a release acceptance claim.
The original export matched the 124-file final experiment freeze; subsequent
integration commits intentionally change selected product files.
The original public frontend, CLI, launcher, skill package and example tree are
retained. Local runtime data and secrets were not copied.

Known integration issues found before this snapshot:

- CLI audit payload does not match the current candidate-audit API (HTTP 422).
- CLI verification choices omit the current native `local_skillwiki` harness.
- CLI startup help advertises SQLite/Postgres although the backend accepts Git/memory.
- Frontend build passes, but its M1 static test rejects any setTimeout; the current
  zero-delay timer defers a real backend read and does not fabricate completion.
- Some regression tests and historical demo helpers reference research fixtures
  outside this source export. Keep the original research workspace for those.
- Full browser interaction and a fresh dependency installation are not accepted yet.

The follow-up CLI integration commit corrects the API contracts, exposes the
native local verification and lifecycle routes, uses persistent Git storage by
default, and documents the real command tree. The CLI still does not retry HTTP
requests or use external agent tools as fallbacks. Candidate audits may call the
configured server model; delivery checks used a model-blocking fixture.
Historical experiment identities and results remain separate from these product
and documentation changes.

## Subsequent product integration (2026-11-10)

The initial findings above describe the export checkpoint, not today's open
issues. Commits on this PR subsequently added:

- `ddea5a4b`: native CLI contracts, commands and persistent server defaults.
- `d6d41756`: G6 callback initialization after rendering.
- `74c6b614`: current repository snapshots and persisted receipt projection.
- `cd6f62fe`: canonical Skill navigation, release request body, graph request
  ordering, malformed receipt isolation and explicit per-step verdict matching.
- `ec7471b1`: reuse successful Git preparation rather than reconfiguring Git
  for every payload read; payload contents remain read from disk.

The initial manifests remain immutable evidence of the exported experiment
source. They are not byte-for-byte manifests of these later commits and do not
claim that the full experiment matrix was rerun on them.

Integration verification includes 11 browser route smoke checks, focused native
release/recovery/graph interactions, CLI contract tests, frontend adapter tests,
lint/build, and storage/graph regressions. The 61-test storage/graph check for
`ec7471b1` passed with exit 0; its local 31-version graph request completed in
approximately 0.3–0.4 seconds after restart, compared with about 20 seconds
before the fix. This is a local performance observation, not a scale benchmark.
The browser/native checks span their recorded integration revisions.

The [public evaluation companion](../public-experiments/README.md) now provides
task materials, source snapshots, aggregate results and offline consistency checks.
The [results page](../EXPERIMENTS.md) separates experimental runtime revisions
from this product integration.

Remaining boundaries: historical research-fixture-dependent tests are not a
portable full-suite acceptance; dependency findings remain under review; the
complete historical runtime and raw-receipt rerun package is not included here. The [user guide](../USER_GUIDE.md)
separates product operations, historical records and paper reproduction.

The documentation follow-up replaces the old blanket timer assertion in
`test-m1-state.mjs`: a zero-delay saved-record fetch is allowed, while actual
component-effect checks require pending calls to leave results empty, backend
failures to show an error, and stale responses after cleanup to be ignored.
The production component is unchanged by this test correction.
