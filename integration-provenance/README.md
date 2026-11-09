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

No product behavior was changed to fix these issues in this snapshot. Preserve
this commit and apply any agreed integration fixes as subsequent commits. Keep
historical experiment identities and results separate from later CLI/UI fixes.
