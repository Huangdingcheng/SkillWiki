---
name: skillwiki-verify
description: Verify a SkillWiki skill using its native local harness and inspect identity-bound evidence.
version: 0.3.0
---

# Verify a skill

Use the running SkillWiki API. Global connection options precede the command:
`skillwiki --api-url http://127.0.0.1:8001 verify <skill_id>`.

```bash
skillwiki skill list --state S2
skillwiki skill get <skill_id> --full
skillwiki verify <skill_id> --harness local_skillwiki --max-attempts 1 --no-repair --no-promote --watch
skillwiki verify <skill_id> --test-cases ./cases.json --json
```

Defaults are native `local_skillwiki`, one total attempt, no repair and no state
promotion. Model-backed verification can incur charges. Use a JSON array of
HarnessTestCase objects for `--test-cases`; see `user-guide-examples/cases.json`.
Inspect the returned status, per-attempt verdicts, score, evidence path and skill
identity. A failed run returns a nonzero exit code. Preserve failed receipts.
Do not replace missing results with example scores or retry completed calls to
obtain a better score. External harnesses are explicit integrations, never fallback.

Use `--promote` only when requested; a passing run may advance an eligible skill
to S3 under the server's checks. Release is a separate authorized operation using
current-identity evidence. A changed or recovered identity needs fresh applicable
verification. Do not use a manual S4 transition to bypass the release workflow.

```bash
skillwiki lifecycle release --help
skillwiki lifecycle new-version --help
```

See the repository `USER_GUIDE.md` for evidence-bundle fields and lifecycle commands.
