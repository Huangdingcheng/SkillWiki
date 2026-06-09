# SkillWiki

**SkillWiki: A Living Knowledge Infrastructure for Agent Skills**

SkillWiki is a living knowledge infrastructure for agent skills. It imports experience from multiple source types, converts that material into governed Skill candidates, manages Skill graph relations and versions, and runs local verification/evaluation workflows — all accessible via a web UI or the `skillwiki` CLI.

**Frontend (default English):** [http://localhost:5173](http://localhost:5173) — switch to 中文 via the language button in the header  
**中文前端（同一地址）：** [http://localhost:5173](http://localhost:5173) — 点击右上角语言按钮切换为中文

## Quick Start On Windows

### 1. Install Dependencies

Backend:

```powershell
cd <repo-root>\skillwiki
python -m pip install -r requirements.txt
```

Frontend:

```powershell
cd <repo-root>\skillwiki-frontend
npm install
```

### 2. Start SkillWiki

Open the repository root and double-click:

```text
START_SKILLWIKI_DEMO.bat
```

On the first run, the terminal asks for:

```text
DeepSeek API URL
DeepSeek model
DeepSeek API key
```

Press Enter to accept the default URL/model if they are correct:

```text
https://api.deepseek.com
deepseek-v4-flash
```

Paste your own API key when prompted. The key input is hidden. The launcher writes the values only to:

```text
skillwiki-launcher\config.local.ps1
```

That file is ignored by Git and must not be committed.

After configuration, the launcher starts:

```text
Backend:  http://127.0.0.1:8001
Frontend: http://127.0.0.1:5174/wiki
```

If those ports are busy, the launcher chooses nearby free ports and prints the actual URL.

### 3. Restore Demo Data

The default demo uses the memory backend, so data is cleared after restart. To restore the small public demo fixtures, double-click:

```text
RESTORE_SKILLWIKI_DEMO_STATE.bat
```

### 4. Stop SkillWiki

Double-click:

```text
STOP_SKILLWIKI_DEMO.bat
```

## CLI Usage

The `skillwiki` command gives agents and scripts direct access to all core operations without a browser.

### Install the CLI

```powershell
cd <repo-root>\skillwiki
venv\Scripts\activate
pip install -e .
```

### Start the backend

```bash
skillwiki serve
# or with options:
skillwiki serve --host 127.0.0.1 --port 8001 --backend memory
```

### Ingest experience

Accepts a file path or raw text. The `SOURCE_TYPE` tells the pipeline how to process the input.

| SOURCE_TYPE | Typical formats | Description |
|---|---|---|
| `trajectory` | `.txt`, `.md` | Operation sequences / conversation traces |
| `document` | `.md`, `.txt` | Knowledge docs, tutorials, specifications |
| `api_doc` | `.md`, `.txt`, `.yaml` | API endpoint documentation |
| `script` | `.sh`, `.md` | Shell / automation scripts (static analysis, not executed) |
| `past_skills` | `.json`, `.jsonl` | Existing skill definitions for bulk import |

```bash
# From file
skillwiki ingest run document ./tutorial.md
skillwiki ingest run script ./installer.sh --create
skillwiki ingest run past_skills ./skills.json --max-candidates 20

# From raw text
skillwiki ingest run trajectory "open browser -> search -> copy link"

# Auto-create S1 candidates after parsing
skillwiki ingest run document ./guide.md --create

# Check ingestion status
skillwiki ingest status <candidate_id>
```

### Verify a skill (execute-verify loop)

Runs the skill, checks postconditions, repairs and retries until pass or max-retries reached.

```bash
# Basic — mock harness, 3 retries, auto-promote to S3 on pass
skillwiki verify <skill_id>

# Watch each attempt, use Claude Code harness, 5 retries
skillwiki verify <skill_id> --harness claude_code --max-retries 5 --watch

# Verify without promoting state
skillwiki verify <skill_id> --no-promote
```

### Audit a skill (static checks)

Checks schema completeness, safety patterns, and postcondition alignment.

```bash
skillwiki audit <skill_id>
```

### Promote lifecycle state

Manually advance a skill through the S0→S4 state machine.

```bash
# States: S0 (raw) → S1 (candidate) → S2 (draft) → S3 (verified) → S4 (released)
skillwiki promote <skill_id> S3
skillwiki promote <skill_id> released
```

### Browse and execute skills

```bash
# List all skills
skillwiki skill list

# Filter by state or tag
skillwiki skill list --state S3
skillwiki skill list --tag pdf

# View skill details
skillwiki skill get <skill_id>
skillwiki skill get <skill_id> --full

# Check current state, version, and success rate
skillwiki skill status <skill_id>

# Execute a skill directly
skillwiki skill exec <skill_id> --input '{"url": "https://example.com"}'
```

### Health monitoring

```bash
# System-wide health overview
skillwiki health

# Per-skill health: success rate, issues, recommendations, open proposals
skillwiki health <skill_id>

# JSON output
skillwiki health --json
skillwiki health <skill_id> --json
```

### Maintenance proposals

```bash
skillwiki proposal list
skillwiki proposal list --status pending
skillwiki proposal list --json

skillwiki proposal accept <proposal_id>
skillwiki proposal reject <proposal_id>
```

### Repair a degraded skill

Generates a maintenance candidate for a skill in S5 (degraded) state:

```bash
skillwiki repair <skill_id>
```

### Evolution cycle

Detects degraded/stale skills and generates maintenance proposals:

```bash
skillwiki evolve
skillwiki evolve --json
```

### Knowledge graph

```bash
# Show direct neighbors
skillwiki graph neighbors <skill_id>
skillwiki graph neighbors <skill_id> --depth 2

# Show a subgraph view
skillwiki graph show <skill_id>
skillwiki graph show <skill_id> --view provenance
skillwiki graph show <skill_id> --view version_impact --depth 3

# Show dependency chain
skillwiki graph deps <skill_id>

# Export subgraph as JSON
skillwiki graph export <skill_id> -o output.json --view provenance --depth 2
```

### Run a natural language task

Dispatches through the full Planner → Retrieval → Execution → Verifier pipeline.

```bash
skillwiki run "analyze this PDF and summarize the key points"
skillwiki run "create an Excel report from this data" --verbose
```

### Global options

```bash
# Point CLI at a non-default backend
skillwiki --api-url http://192.168.1.10:8001 skill list

# All commands support --help
skillwiki health --help
skillwiki proposal --help
skillwiki graph --help
skillwiki evolve --help
```

## Useful Pages

```text
http://127.0.0.1:5174/wiki
http://127.0.0.1:5174/ingest
http://127.0.0.1:5174/graph
http://127.0.0.1:5174/harness
http://127.0.0.1:5174/evaluation
http://127.0.0.1:5174/versions
```

Recommended demo order:

1. Skill Wiki
2. Knowledge Import
3. Knowledge Graph
4. Harness Verification
5. Evaluation
6. Version Control

## What This Branch Demonstrates

- Five input types: `trajectory`, `document`, `api_doc`, `script`, and `past_skills`.
- Ctx2Skill-lite evidence for document-to-skill extraction.
- SkillX-style layer metadata: `atomic`, `functional`, and `strategic`.
- Skill graph visualization with Nebula/Readable/Debug presets.
- Version Lab with business-readable diffs and re-verification after implementation/interface changes.
- Local harness verification for selected Skills.
- SkillsBench P0 sparse-subset analysis: oracle `5/5`, no-skill `2/5`, SkillWiki generated skills `3/5` using clean combined evidence.

## Documentation

Start here:

```text
DEMO_STARTUP.md
docs\SKILLOS_PR_UPLOAD_SCOPE_AND_DEMO_HANDOFF_20260529.md
docs\SKILLOS_PR_UPDATE_SUMMARY_AFTER_SKILLSBENCH_20260529.md
docs\SKILLOS_SKILLSBENCH_FIVE_TASK_DEEP_ANALYSIS_20260529.md
```

## PR Safety Notes

Commit code, lightweight fixtures, scripts, and documentation. Do not commit:

- `skillwiki-launcher\config.local.ps1`
- `skillwiki-launcher\runtime\`
- raw benchmark run directories
- Docker installers/images/cache
- `.venv`, `node_modules`, or build caches
- API keys or local credentials
