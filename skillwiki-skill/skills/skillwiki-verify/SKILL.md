---
name: skillwiki-verify
description: Use this skill when the user wants to "verify a skill", "run the verify loop", "test a skill until it passes", "check if a skill meets its postconditions", "repair and retry a skill", or "promote a skill to S3 verified". This skill runs the execute-verify-repair loop using the skillwiki CLI until the skill passes or retries are exhausted.
version: 0.1.0
---

# SkillWiki Verify Loop

You run the execute-verify-repair loop for a skill candidate, retrying with automated repairs until the skill's postconditions pass or the maximum retry count is reached.

## How the loop works

```
S2 (draft)
  ↓ harness executes skill with test input
  ↓ verifier checks postconditions
  ↓ FAIL → Skill Builder repairs implementation → retry
  ↓ PASS → auto-promote to S3 (verified)
S3 (verified)
```

## Get the skill ID

If the user does not have the ID, list available candidates:

```bash
skillwiki skill list --state S2
```

## Run the verify loop

```bash
# Default: mock harness, 3 retries, auto-promote on pass
skillwiki verify <skill_id>

# Watch each attempt as it runs
skillwiki verify <skill_id> --watch

# Use Claude Code harness (requires Claude Code CLI installed)
skillwiki verify <skill_id> --harness claude_code --max-retries 5 --watch

# Use Codex harness
skillwiki verify <skill_id> --harness codex --max-retries 5 --watch

# Verify without auto-promoting state
skillwiki verify <skill_id> --no-promote
```

## Interpreting results

**Pass:**
```
✓ Verified in 2 attempt(s) — score: 0.92
  State promoted to: S3
```

**Fail:**
```
✗ Verification failed after 3 attempt(s) — score: 0.41
  Repair attempts: 3
```

If verification fails, check the skill's implementation:

```bash
skillwiki skill get <skill_id> --full
```

Then either fix the implementation manually and re-run, or raise `--max-retries` to give the auto-repair more attempts.

## Harness options

| Harness | When to use |
|---|---|
| `mock` | Quick sanity check, no LLM needed |
| `claude_code` | Production validation using Claude Code as executor |
| `codex` | Validation using OpenAI Codex as executor |

## After verification

Once a skill reaches S3, promote it to released when ready:

```bash
skillwiki promote <skill_id> S4
```
