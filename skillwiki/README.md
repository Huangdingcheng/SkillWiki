# SkillWiki — A Living Knowledge Infrastructure for Agent Skills

SkillWiki is a research system that models Skills as versioned, auditable, graph-connected knowledge objects. It supports the full lifecycle from raw knowledge ingestion to governed evolution: S0 (raw) → S1 (candidate) → S2 (draft) → S3 (verified) → S4 (released) → S5 (degraded) → S6 (deprecated) → S7 (archived).

## Quick Start

Follow the repository [installation and startup instructions](../README.md#quick-start)
for the Python environment, persistent Git backend and correctly configured
frontend proxy. Use Python 3.10+; the locked frontend toolchain needs Node.js
20.19+ or 22.12+.

```bash
# From this directory, with your virtual environment active:
python -m pip install -e ".[api]"
# Set LLM_API_KEY, LLM_API_URL and LLM_MODEL in this terminal first.
git init ./governance
skillwiki serve --host 127.0.0.1 --port 8001 --backend git --storage-dir ./skillwiki-data --governance-repo ./governance --llm-retries 0
```

Keep the server running. API health: <http://127.0.0.1:8001/health>;
request schemas: <http://127.0.0.1:8001/docs>.
The [user guide](../USER_GUIDE.md) covers the CLI request files and the web workflow.

## CLI Reference

All commands share a global `--api-url` option (default `http://127.0.0.1:8001`).

### Server

```bash
skillwiki serve [--host HOST] [--port PORT] [--backend git|memory]
```

### Knowledge Ingestion

```bash
# Parse raw content and extract skill candidates
skillwiki ingest run <source_type> <input>
    source_type: trajectory | document | api_doc | script | past_skills

# Examples
skillwiki ingest run api_doc ./openai_spec.md
skillwiki ingest run trajectory "open browser -> search -> copy link"
skillwiki ingest run past_skills ./skills.json --create

# Show candidate state
skillwiki ingest status <candidate_id>
```

### Lifecycle

```bash
# Show skill state
skillwiki skill status <skill_id>

# List skills (with optional filters)
skillwiki skill list [--state S3] [--tag nlp] [--limit 20]

# Get skill details
skillwiki skill get <skill_id> [--full]

# Execute a skill
skillwiki skill exec <skill_id> --input '{"key": "value"}'

# Non-persisting candidate audit (may call the configured server model)
skillwiki audit <skill_id>

# Execute-verify loop with optional auto-promote
skillwiki verify <skill_id> [--harness local_skillwiki] [--max-attempts 1] [--watch]

# Manually promote to a target state
skillwiki promote <skill_id> <target_state>
    target_state: S0 | S1 | S2 | S3 | S4 | S5 | S6 | S7
```

### Health & Evolution

```bash
# System-wide health overview
skillwiki health

# Per-skill health (success rate, issues, recommendations, open proposals)
skillwiki health <skill_id>

# Generate a maintenance candidate for a degraded skill
skillwiki repair <skill_id>

# Run one full evolution cycle (detect degraded/stale skills, queue proposals)
skillwiki evolve [--json]
```

### Maintenance Proposals

```bash
# List proposals
skillwiki proposal list [--status pending|accepted|rejected] [--json]

# Accept a proposal (moves to governed version review)
skillwiki proposal accept <proposal_id>

# Reject a proposal
skillwiki proposal reject <proposal_id>
```

### Knowledge Graph

```bash
# Show direct neighbors of a skill
skillwiki graph neighbors <skill_id> [--depth 1]

# Show provenance / version-impact subgraph
skillwiki graph show <skill_id> [--view skill_only|provenance|version_impact] [--depth 2]

# Show dependency chain
skillwiki graph deps <skill_id>

# Export subgraph as JSON
skillwiki graph export <skill_id> [-o output.json] [--view provenance] [--depth 2]
```

### Natural Language Task Execution

```bash
skillwiki run "summarize the attached PDF and extract action items" [--verbose]
```

### Utilities

`init` and `ping` are provider-configuration/connectivity utilities. Their local
`--api-url` refers to the model provider, unlike the root CLI option that points
to SkillWiki. They are not required to start the server with environment values;
`ping` can make a provider request. Prefer the environment-based startup in the
user guide for normal use.

```bash
skillwiki init --api-key KEY [--api-url URL] [--model MODEL]
skillwiki ping --api-key KEY
```

---

## Lifecycle States

| State | Code | Meaning |
|-------|------|---------|
| Raw knowledge | S0 | Ingested document / trajectory |
| Candidate | S1 | Extracted skill candidate |
| Draft | S2 | Formalized schema, pending review |
| Verified | S3 | Passed automated verification |
| Released | S4 | Approved for agent use |
| Degraded | S5 | Success rate below threshold |
| Deprecated | S6 | Replaced or retired |
| Archived | S7 | Read-only historical record |

---

## Evolution Flow

```
Execution feedback
      |
      v
Reflection memory   (recurring failures trigger a cluster)
      |
      v
Maintenance Proposal  (repair | review | deprecate)
      |
      v
Human governance      (skillwiki proposal accept/reject)
      |
      v
Version update        (new versioned skill, graph edges updated)
```

---

## Project Structure

```
skillwiki/
├── skillwiki/
│   ├── api/              # FastAPI backend
│   │   └── routes/       # evolution, graph, lifecycle, execution, ...
│   ├── cli.py            # Click CLI
│   ├── config/           # LLM + app configuration
│   ├── layers/
│   │   ├── input_knowledge/     # S0 ingestion & parsing
│   │   ├── skill_construction/  # S1-S2 candidate mining & formalization
│   │   ├── skill_governance/    # S3-S4 review, merger, versioning
│   │   ├── skill_management/    # librarian, graph sync
│   │   ├── skill_runtime/       # executor, verifier, harness
│   │   └── feedback_evolution/  # monitor, repair, evolution engine
│   ├── models/           # Pydantic data models
│   └── storage/          # Skill repository (memory / git-backed)
├── tests/
├── config.yaml
└── requirements.txt
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LLM_API_KEY` | — | LLM API key (required) |
| `LLM_API_URL` | `https://api.deepseek.com` (API server) | Provider base URL; set explicitly |
| `LLM_MODEL` | `deepseek-flash` (API server) | Model available to your provider account; set explicitly |
| `SKILLOS_API_TARGET` | `http://127.0.0.1:8000` (bare Vite) | Set to the actual backend URL; quick start uses port 8001 |

---

## License

MIT


## Detailed native CLI workflow

See the [shared user guide](../USER_GUIDE.md#cli-workflow) for candidate review,
verification cases, execution receipts, new versions, snapshots, evidence-bound
release and recovery. Both interfaces use the native API; no external agent is
automatically substituted when a local run fails.
