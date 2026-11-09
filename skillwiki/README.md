# SkillWiki — A Living Knowledge Infrastructure for Agent Skills

SkillWiki is a research system that models Skills as versioned, auditable, graph-connected knowledge objects. It supports the full lifecycle from raw knowledge ingestion to governed evolution: S0 (raw) → S1 (candidate) → S2 (draft) → S3 (verified) → S4 (released) → S5 (degraded) → S6 (deprecated) → S7 (archived).

## Quick Start

### Requirements

- Python 3.10+
- Node.js 18+ (frontend only)

### Install

```bash
git clone https://github.com/Huangdingcheng/SkillWiki.git
cd SkillWiki/skillwiki

python -m venv venv
source venv/bin/activate        # Linux/Mac
venv\Scripts\activate           # Windows

pip install -r requirements.txt
pip install -e ".[api]"
```

### Start the backend

```bash
skillwiki serve --port 8001 --api-key YOUR_LLM_API_KEY
# or
python -m skillwiki.api.main --port 8001 --api-key YOUR_LLM_API_KEY
```

### Start the frontend (optional)

```bash
cd ../skillwiki-frontend
npm install
npm run dev        # http://localhost:3000
```

---

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
| `LLM_API_URL` | `https://yunwu.ai` | LLM base URL |
| `LLM_MODEL` | `claude-sonnet-4-6` | Model name |
| `SKILLOS_API_TARGET` | `http://127.0.0.1:8001` | Frontend proxy target |

---

## License

MIT


## Native CLI workflow

The CLI calls the same API as the web UI. Install the API extra with
`pip install -e ".[api]"` from the `skillwiki` backend directory. Use
`skillwiki --help` and `skillwiki <command> --help` for the full command tree.

```bash
# Persistent Git storage, no synthetic seed data, zero LLM transport retries.
# Set LLM_API_KEY in the environment; avoid putting credentials in shell history.
skillwiki serve --backend git --storage-dir ./skillwiki-data --governance-repo ./governance --llm-retries 0
# Optional: --model MODEL --llm-api-url URL. memory is an ephemeral alternative.

# Global --api-url is the SkillWiki server, not the LLM provider endpoint.
skillwiki --api-url http://127.0.0.1:8001 skill list --tag nlp
skillwiki skill search "extract structured records" --mode lexical
skillwiki ingest run document ./source.md
# Edit the parsed draft into the native candidate-review request JSON.
skillwiki ingest audit-candidate --file candidate.json
skillwiki ingest create-candidate --file candidate.json
skillwiki audit SKILL_ID --json

# Calls the native runtime. Ingestion, auditing and verification may incur model costs.
# Default: one total attempt, no repair, no promotion. No automatic fallback.
skillwiki verify SKILL_ID --test-cases cases.json --json
# Explicit opt-ins: --allow-repair --max-attempts 3 --promote
skillwiki skill exec SKILL_ID --input '{"text":"example"}'
skillwiki execution history
skillwiki execution get EXECUTION_ID
skillwiki execution trace EXECUTION_ID
skillwiki execution experience EXECUTION_ID

# Lifecycle commands pass the native API request object unchanged.
skillwiki lifecycle new-version SKILL_ID --file version.json
skillwiki skill versions SKILL_ID
skillwiki lifecycle snapshot SKILL_ID --file snapshot.json
skillwiki lifecycle history SKILL_ID
skillwiki lifecycle diff SKILL_ID --from-ref COMMIT --to-ref HEAD
skillwiki lifecycle release SKILL_ID --file release-evidence.json
skillwiki lifecycle rollback SKILL_ID --file recovery.json
```

Request file examples (UTF-8 JSON):

- `candidate.json`: `{"source_type":"document","unit_id":"source-1","raw_content":"Return a structured result.","name":"structured_result","description":"Return a structured result.","prompt_template":"Return {\"result\":true}","prompt_format":"literal"}`.
- `cases.json`: an array of native harness cases, e.g. `[{"test_id":"case-1","name":"result present","goal":"Return the result","input_data":{},"verifier_specs":[{"type":"json_exists","path":"output.result"}]}]`.
- `version.json`: `{"bump":"patch","description":"Updated documented behavior"}`; include `interface` or `implementation` objects when changing behavior.
- `snapshot.json`: `{"author":"reviewer","message":"Review candidate snapshot"}`.
- `recovery.json`: `{"source_ref":"COMMIT","source_version":"1.0.0","reason":"Review recovery draft"}`.
- `release-evidence.json`: the complete native `EvidenceReleaseRequest` (`schema_version`, `manifest`, `traces`, `evaluation`, optional `author`); use actual identity-bound run evidence, not a copied success flag. The running server's `/docs` describes the schema.

`audit` is a non-persisting draft contract check that may use the server-configured model; it does not grant validation or
release evidence to the stored identity. `new-version` and `rollback` create
separate drafts; verify and release their new identities afresh. `promote` remains
a manual transition command, subject to server gates. Evidence-bound release uses
`lifecycle release`.

`verify` returns exit code 1 for any non-passing outcome and prints the native score
object and evidence path. Audits, skill/task execution and file-based governance
commands also return failure exits when the server reports failure. JSON output
is available on audit, verify and lifecycle/readback commands. The CLI never retries
HTTP calls; after a timeout, inspect server execution history before resubmitting.
The global `--http-timeout` controls the HTTP deadline (default 600 seconds).
`--max-retries` is retained only as an alias for **total** `--max-attempts`.
External `claude_code` and `codex_cli` adapters are explicit selections with their
own environment requirements; they are never used as native execution fallbacks.
