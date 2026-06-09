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
pip install -e .
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
skillwiki serve [--host HOST] [--port PORT] [--backend memory|sqlite|postgres]
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

# Static audit (schema, safety, postconditions)
skillwiki audit <skill_id>

# Execute-verify loop with optional auto-promote
skillwiki verify <skill_id> [--harness mock|claude_code|codex] [--max-retries 3] [--watch]

# Manually promote to a target state
skillwiki promote <skill_id> <target_state>
    target_state: S1 | S2 | S3 | released | ...
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
