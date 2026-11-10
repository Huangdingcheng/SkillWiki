# SkillWiki frontend

React, TypeScript, Ant Design and G6 interface for the SkillWiki API. The existing
layout and graph styling are retained; the integration connects canonical Skill
identities, persisted graph evidence, lifecycle requests and recovery drafts.

For users: [quick start](../README.md#quick-start), [user guide](../USER_GUIDE.md),
and [中文指南](../USER_GUIDE.zh-CN.md).

## Run against a local backend

Use Node.js **20.19+ or 22.12+**, as required by the locked Vite dependencies.
Start the backend first at `http://127.0.0.1:8001` using the root quick start.
From this directory:

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
# Bash, Linux / macOS
SKILLOS_API_TARGET=http://127.0.0.1:8001 VITE_SKILLOS_DISABLE_WS=1 npm run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

Run only the block for your shell. Open <http://127.0.0.1:5174/wiki>.
The Windows [launcher](../skillwiki-launcher/README.md) configures the actual
proxy target and disables WebSocket by default.

Bare `npm run dev` uses Vite port **5173** and a backend target of
`http://127.0.0.1:8000` unless overridden. The documented commands explicitly
select **5174 / 8001**. `/api` and `/ws` are proxied; `/health` is a backend URL,
not a frontend health endpoint. Test the proxy with
<http://127.0.0.1:5174/api/v1/skills?limit=1>.

## Pages and API mapping

| Route | Backend records/actions |
|---|---|
| `/` | Dashboard and recorded health/usage |
| `/wiki` | Skill catalog, full records and lifecycle actions |
| `/ingest` | Parsing, edited candidate audit, creation and draft transition |
| `/harness` | Native verification loops and persisted attempt receipts |
| `/execution` | Task execution, traces, verifier details and experience |
| `/lifecycle` | Skill review and state transitions |
| `/versions` | New drafts, Git snapshots, structured diffs, tags and recovery |
| `/graph` | `/api/v1/graph/view` with `skill_only`, `provenance`, `version_impact`; related expansion uses POST `/api/v1/graph/subgraph` |
| `/evolution` | Health, maintenance proposals and explicit evolution actions |
| `/evaluation` | Available evaluation records with their original scope |
| `/demo` | Explicit self-evolution demonstration controls |

Skill navigation carries canonical `skill_id`, not a graph display-node ID.
Graph data are rebuilt from persisted Skill versions and applicable saved event
records. See [graph semantics](../USER_GUIDE.md#reading-the-graph) before
interpreting an edge or verdict. UI rendering is not qualification evidence.

The web Release button calls the existing stored-evidence release route.
The CLI additionally exposes the separate evidence-bundle submission route;
the UI does not edit every research manifest field.

## Checks

From this directory, with dependencies installed:

```bash
npm run lint
npm run build
node scripts/test-graph-adapter.mjs
node scripts/test-lifecycle-request.mjs
node scripts/test-m1-state.mjs
```

The graph, state and release adapter checks execute actual component/client logic with
controlled boundaries. They complement browser interaction and native API tests;
they do not rerun the paper's model experiments. Backend contract checks, from
`../skillwiki` after installing `.[api,dev]`:

```bash
python -m pytest tests/test_cli_contract.py tests/test_graph_repository_refresh.py tests/test_graph_receipt_projection.py -q --disable-warnings
```

`npm run build` generates `dist/`. A production server must separately configure
API forwarding and SPA fallback; the Vite development proxy is not automatically
part of a static deployment. This guide validates the local development route.

## Known maintenance items

The 2026-11-10 audit recorded 27 dependency findings (15 high, 11 moderate, 1 low)
and existing Ant Design deprecation warnings. Dependency versions were not
upgraded in this integration; review `npm audit` against the installed lockfile
before deployment. The existing build chunk-size advisory is separate from
runtime correctness. Empty or historical fixture data must retain their actual
meaning and must not be relabeled as fresh experiment results.
