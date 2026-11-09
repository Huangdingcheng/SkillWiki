# Local product source candidate — 2026-11-09

This branch isolates the actual current product source from the shared research
workspace. It is not a new experimental result or a release acceptance claim.
The 124 Python product files must match the linked final experiment freeze.
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
