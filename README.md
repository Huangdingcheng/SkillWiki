<div align="center">

# SkillWiki

**A Living Knowledge Infrastructure for Agent Skills**

Just as Wikipedia organizes human knowledge and GitHub governs software, **SkillWiki** provides the missing infrastructure for agent skills. It transforms heterogeneous knowledge sources — trajectories, documents, API specs, and execution experience — into reusable skill assets that are grounded in their originating evidence, version-controlled, and continuously evolved. SkillWiki covers the complete skill lifecycle: from knowledge ingestion and skill production, through provenance-aware graph exploration and human governance, to execution-driven health monitoring and automated evolution. The result is a shared infrastructure where knowledge, skills, and execution experience co-evolve together.

<br/>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Node](https://img.shields.io/badge/Node.js-20.19%2B%20%7C%2022.12%2B-339933?logo=node.js&logoColor=white)](https://nodejs.org/)
[![Stars](https://img.shields.io/github/stars/Huangdingcheng/SkillWiki?style=social)](https://github.com/Huangdingcheng/SkillWiki)


<br/>

[📺 **Demo Video (English)**](https://youtu.be/2n7n75j6v14)
&nbsp;·&nbsp;
[📺 **Demo Video (Chinese)**](https://youtu.be/HNcsK-090bU)
&nbsp;·&nbsp;
[🚀 **Live System (HF Space)**](https://huggingface.co/spaces/Huangdingcheng/SkillWiki)
&nbsp;·&nbsp;
[📖 **Paper**](https://arxiv.org/abs/2606.16523)
</div>

---

<p align="center">
  <img src="figures/figure2_overview/overview.png" width="900" alt="SkillWiki system overview"/>
  <br/>
  <em>SkillWiki system overview — from multi-source ingestion to governed skill evolution.</em>
</p>

---

## Key Features

| | Feature | Description |
|:---:|---|---|
| 📥 | **Multi-source ingestion** | Trajectories, documents, API specs, scripts, and existing skill JSON/JSONL files |
| 🔄 | **Governed lifecycle** | Eight-stage state machine (S0–S7) with automated verification and full audit trail |
| 🕸️ | **Provenance knowledge graph** | Typed edges — `depends_on`, `composes_with`, `evolved_from`, `replaces`, and more |
| 🩺 | **Health & evolution engine** | Continuous monitoring detects degraded skills and queues repair proposals automatically |
| 🌐 | **Bilingual UI** | English (default) and Chinese — toggle via the language button in the header |
| ⌨️ | **CLI-first** | Native commands for ingestion, verification, governance, execution and inspection |

---

## Lifecycle

```
  S0 Raw  ──►  S1 Candidate  ──►  S2 Draft  ──►  S3 Verified  ──►  S4 Released
                                                                          │
                                             S7 Archived  ◄──  S6 Deprecated  ◄──  S5 Degraded
```

| State | Name | Meaning |
|:---:|---|---|
| **S0** | Raw | Ingested source material, not yet extracted |
| **S1** | Candidate | Extracted skill candidate awaiting review |
| **S2** | Draft | Formalized schema, pending verification |
| **S3** | Verified | Passed automated postcondition checks |
| **S4** | Released | Approved for agent use in production |
| **S5** | Degraded | Success rate fell below health threshold |
| **S6** | Deprecated | Replaced or retired |
| **S7** | Archived | Read-only historical record |

---

## Quick Start

### Requirements

- Git and Python 3.10+.
- Node.js 20.19+ or 22.12+ (frontend; required by the locked Vite toolchain).
- Your own OpenAI-compatible model endpoint, model name and API key for model-backed operations.

### Install

```bash
git clone https://github.com/Huangdingcheng/SkillWiki.git
cd SkillWiki/skillwiki
python -m venv .venv
```

Activate the environment, then install the backend:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# Linux / macOS
source .venv/bin/activate
```

```bash
python -m pip install -e ".[api]"
```

### Start the backend

Set `LLM_API_KEY`, `LLM_API_URL` and `LLM_MODEL` in this terminal using your own
provider configuration. See the [user guide](USER_GUIDE.md#model-configuration)
for shell examples. Keep this terminal running:

```bash
git init ./governance
skillwiki serve --host 127.0.0.1 --port 8001 --backend git --storage-dir ./skillwiki-data --governance-repo ./governance --llm-retries 0
```

Backend health: <http://127.0.0.1:8001/health>. API schemas: <http://127.0.0.1:8001/docs>.

### Start the frontend (optional)

In a second terminal, enter `SkillWiki/skillwiki-frontend`, install the locked
dependencies and explicitly point the development proxy at the backend:

```bash
npm ci
```

```powershell
# Windows PowerShell
$env:SKILLOS_API_TARGET = "http://127.0.0.1:8001"
$env:VITE_SKILLOS_DISABLE_WS = "1"
npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

```bash
# Linux / macOS
SKILLOS_API_TARGET=http://127.0.0.1:8001 VITE_SKILLOS_DISABLE_WS=1 npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Open <http://127.0.0.1:5174/wiki>. Run only the command block for your shell.

### One-click launcher (Windows)

After installing the backend and frontend dependencies above, double-click
**`START_SKILLWIKI_DEMO.bat`** in the repository root. The launcher starts both
services and prompts for missing model configuration. Its local configuration
and runtime data are Git-ignored. See the [launcher guide](skillwiki-launcher/README.md).
The launcher may choose another free port; use the URL printed at startup.

New stores are empty by default. Skills persist across restarts with the Git
backend. `RESTORE_SKILLWIKI_DEMO_STATE.bat` explicitly adds public demonstration
fixtures and can run verification/model-backed operations; it is not needed to
recover an ordinary persisted store and does not reproduce paper results.

---

## Demo Walkthrough

Step-by-step instructions: [User guide](USER_GUIDE.md) · [中文使用指南](USER_GUIDE.zh-CN.md) · [Frontend setup](skillwiki-frontend/README.md).

| Step | Page | What to explore |
|:---:|---|---|
| 1 | [`/wiki`](http://127.0.0.1:5174/wiki) | Skill library — browse, filter by state / tag, inspect schemas |
| 2 | [`/ingest`](http://127.0.0.1:5174/ingest) | Paste an API doc or upload a file; watch candidates extracted live |
| 3 | [`/graph`](http://127.0.0.1:5174/graph) | Skill-only, Provenance and Version impact views; inspect persisted identities and receipts |
| 4 | [`/harness`](http://127.0.0.1:5174/harness) | Select Local; inspect attempts and verdicts; repair and promotion are explicit options |
| 5 | [`/evaluation`](http://127.0.0.1:5174/evaluation) | Inspect the available evaluation records; imported historical results remain historical |
| 6 | [`/versions`](http://127.0.0.1:5174/versions) | Business-readable diffs and re-verification after changes |

---

## CLI Reference

Install once after cloning:

```bash
cd skillwiki && pip install -e ".[api]"
```

Place global options before the subcommand: `skillwiki --api-url <URL> <command>`. All commands accept `--api-url <URL>` (default `http://127.0.0.1:8001`).

### Server

```bash
skillwiki serve [--host HOST] [--port PORT] [--backend git|memory]
```

### Knowledge Ingestion

```bash
# source_type: trajectory | document | api_doc | script | past_skills
skillwiki ingest run <source_type> <input> [--create]

skillwiki ingest run api_doc     ./openai_spec.md --create
skillwiki ingest run trajectory  "open browser -> search -> copy link"
skillwiki ingest run past_skills ./skills.json --max-candidates 20
skillwiki ingest status <candidate_id>
```

### Skill Lifecycle

```bash
skillwiki skill list    [--state S3] [--tag nlp] [--limit 20]
skillwiki skill get     <skill_id>   [--full]
skillwiki skill status  <skill_id>
skillwiki skill exec    <skill_id>   --input '{"key": "value"}'

skillwiki audit   <skill_id>
skillwiki verify  <skill_id>  [--harness local_skillwiki] [--max-attempts 1] [--watch]
skillwiki promote <skill_id>  <target_state>
```

### Health & Evolution

```bash
skillwiki health             [--json]   # system-wide overview
skillwiki health <skill_id>  [--json]   # success rate, issues, open proposals

skillwiki repair <skill_id>             # generate maintenance candidate
skillwiki evolve             [--json]   # run one full evolution cycle
```

### Maintenance Proposals

```bash
skillwiki proposal list    [--status pending|accepted|rejected] [--json]
skillwiki proposal accept  <proposal_id>
skillwiki proposal reject  <proposal_id>
```

### Knowledge Graph

```bash
skillwiki graph neighbors  <skill_id>  [--depth 1]
skillwiki graph show       <skill_id>  [--view skill_only|provenance|version_impact] [--depth 2]
skillwiki graph deps       <skill_id>
skillwiki graph export     <skill_id>  [-o output.json] [--view provenance] [--depth 2]
```

### Natural Language Execution

```bash
skillwiki run "summarize the attached PDF and extract action items" [--verbose]
```

---

## What SkillWiki Provides

- Knowledge-to-skill transformation from heterogeneous sources.
- Evidence-grounded skill assets with explicit provenance.
- Multi-granularity skill organization and dependency management.
- Provenance-aware exploration and visualization.
- Versioned skill governance with interpretable evolution.
- Execution-driven verification and continuous improvement.
- End-to-end demonstrations of reusable skill ecosystems.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LLM_API_KEY` | — | LLM API key *(required)* |
| `LLM_API_URL` | `https://api.deepseek.com` (API server) | OpenAI-compatible provider base URL; set explicitly |
| `LLM_MODEL` | `deepseek-flash` (API server) | Provider model identifier; set one available to your account |
| `SKILLOS_API_TARGET` | `http://127.0.0.1:8000` (bare Vite) | Set to `http://127.0.0.1:8001` for the commands above; launcher sets its actual port |
| `VITE_SKILLOS_DISABLE_WS` | unset | Set `1` for the local polling experience; launcher does this by default |

---

## License

MIT © 2026 SkillWiki Authors


## User documentation and experiment scope

- [User guide](USER_GUIDE.md): model configuration, CLI request files, lifecycle, graph evidence and troubleshooting.
- [中文使用指南](USER_GUIDE.zh-CN.md): 本地启动、主要操作、CLI 与前端对应关系。
- [Frontend developer guide](skillwiki-frontend/README.md): routes, proxy configuration and checks.
- [Source provenance](integration-provenance/README.md): experiment snapshot versus subsequent product integration.

These instructions run the product. The paper's frozen inputs, candidates, raw
receipts and analysis package are not included in this product PR. A fresh model
run creates new evidence and may differ from the reported answers; the UI's
public demo fixtures are not the paper's result dataset.
