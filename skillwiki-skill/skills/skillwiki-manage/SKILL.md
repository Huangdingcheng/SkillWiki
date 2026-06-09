---
name: skillwiki-manage
description: Use this skill when the user wants to "list skills", "find a skill", "execute a skill", "run a task with SkillWiki", "check skill status", "check skill health", "run evolution cycle", "accept or reject a proposal", "repair a skill", "explore the knowledge graph", "promote a skill", "audit a skill", "start SkillWiki", or "query the skill wiki". This skill covers general skill management, health monitoring, evolution, and graph operations using the skillwiki CLI.
version: 0.2.0
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

# Check current state, version, success rate and tags
skillwiki skill status <skill_id>
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

## Health monitoring

```bash
# System-wide health overview (all skills)
skillwiki health

# Per-skill health: success rate, issues, recommendations, open proposals
skillwiki health <skill_id>

# Output as JSON
skillwiki health --json
skillwiki health <skill_id> --json
```

## Maintenance proposals

Proposals are generated automatically by the evolution engine when skills degrade.

```bash
# List proposals
skillwiki proposal list
skillwiki proposal list --status pending
skillwiki proposal list --status accepted
skillwiki proposal list --json

# Accept a proposal (triggers governed version review)
skillwiki proposal accept <proposal_id>

# Reject a proposal
skillwiki proposal reject <proposal_id>
```

## Repair a degraded skill

Generates a maintenance candidate for a skill in S5 (degraded) state:

```bash
skillwiki repair <skill_id>
```

## Evolution cycle

Detects degraded/stale skills, generates maintenance proposals, and queues repairs:

```bash
skillwiki evolve
skillwiki evolve --json
```

## Knowledge graph

```bash
# Show direct neighbors (depth 1 by default)
skillwiki graph neighbors <skill_id>
skillwiki graph neighbors <skill_id> --depth 2

# Show a subgraph view
skillwiki graph show <skill_id>
skillwiki graph show <skill_id> --view provenance       # provenance subgraph
skillwiki graph show <skill_id> --view version_impact   # version impact subgraph
skillwiki graph show <skill_id> --view skill_only --depth 3

# Show dependency chain
skillwiki graph deps <skill_id>

# Export subgraph as JSON
skillwiki graph export <skill_id>
skillwiki graph export <skill_id> -o output.json --view provenance --depth 2
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
skillwiki health --help
skillwiki proposal --help
skillwiki graph --help
skillwiki evolve --help
```
