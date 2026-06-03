---
name: skillwiki-manage
description: Use this skill when the user wants to "list skills", "find a skill", "execute a skill", "run a task with SkillWiki", "check skill status", "promote a skill", "audit a skill", "start SkillWiki", or "query the skill wiki". This skill covers general skill management operations using the skillwiki CLI.
version: 0.1.0
---

# SkillWiki Management

You help the user manage, query, and execute skills in the SkillWiki knowledge base.

## Start the backend

```bash
skillwiki serve                          # memory backend (default, no persistence)
skillwiki serve --backend sqlite         # persist to local SQLite
skillwiki serve --port 8001 --api-key <key>
```

## Query skills

```bash
# List all skills (default limit 20)
skillwiki skill list

# Filter by lifecycle state
skillwiki skill list --state S3          # verified only
skillwiki skill list --state S4          # released only

# Filter by tag
skillwiki skill list --tag pdf

# View skill summary
skillwiki skill get <skill_id>

# View full schema including implementation and postconditions
skillwiki skill get <skill_id> --full
```

## Lifecycle states

| State | Meaning |
|---|---|
| S0 | Raw experience (not yet extracted) |
| S1 | Candidate (extracted, awaiting review) |
| S2 | Draft (under verification) |
| S3 | Verified (postconditions passed) |
| S4 | Released (production-ready) |
| S5 | Degraded (health issues detected) |
| S6 | Deprecated |
| S7 | Archived |

## Execute a skill directly

```bash
skillwiki skill exec <skill_id> --input '{"key": "value"}'
```

The input must be a JSON object matching the skill's input schema.

## Run a natural language task

Dispatches through the full Planner → Retrieval → Composition → Execution pipeline:

```bash
skillwiki run "analyze this PDF and extract the key findings"
skillwiki run "create an Excel summary from this data" --verbose
```

## Audit and promote

```bash
# Static audit (schema, safety, postcondition checks)
skillwiki audit <skill_id>

# Manually advance lifecycle state
skillwiki promote <skill_id> S3
skillwiki promote <skill_id> released
```

## Check ingest status

```bash
skillwiki ingest status <candidate_id>
```

## Global options

```bash
# Point to a non-default backend
skillwiki --api-url http://192.168.1.10:8001 skill list

# All commands support --help
skillwiki verify --help
skillwiki ingest run --help
```
