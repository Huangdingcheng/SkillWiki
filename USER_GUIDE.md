# SkillWiki user guide

Use the [quick start](README.md#quick-start) to install the product and start the
Git backend and frontend. [中文指南](USER_GUIDE.zh-CN.md) covers the same main workflow.

## Model configuration

The CLI connects to SkillWiki; the SkillWiki server connects to your model
provider. Configure the provider in the terminal that starts the backend:

```powershell
# Windows PowerShell: enter your provider values when prompted.
$env:LLM_API_URL = Read-Host 'OpenAI-compatible base URL'
$env:LLM_MODEL = Read-Host 'Model identifier'
$modelSecret = Read-Host 'API key' -AsSecureString
$env:LLM_API_KEY = [System.Net.NetworkCredential]::new('', $modelSecret).Password
```

```bash
# Bash: enter your provider values when prompted.
read -r -p 'OpenAI-compatible base URL: ' LLM_API_URL
read -r -p 'Model identifier: ' LLM_MODEL
read -r -s -p 'API key: ' LLM_API_KEY
export LLM_API_URL LLM_MODEL LLM_API_KEY
```

The Windows launcher can prompt for and save these values in its Git-ignored
`skillwiki-launcher/config.local.ps1`. Set `SKILLOS_PYTHON` there if it should use
a particular installed Python environment. Do not put credentials into request
JSON or source control. Ingestion, audit, model-backed execution and repair can
incur provider costs. Browsing existing records does not need a model call.

## Output tokens and truncated responses

The API server defaults to **16,384 output tokens per model call**. This applies
to both `skillwiki serve` and the Windows launcher. Override it in the backend
terminal or, for the launcher, its ignored `config.local.ps1`:

```powershell
$env:LLM_MAX_TOKENS = "16384"
```

```bash
export LLM_MAX_TOKENS=16384
```

Restart the backend after changing this value. Existing local overrides take
precedence over the new default. The server reads the process environment; it
does not automatically load `skillwiki/.env.example` or a copied `.env` file.
Docker Compose forwards `LLM_MAX_TOKENS` from the shell/root `.env` to the API.

This is an **output ceiling**, not the amount always consumed, an input allowance,
or the model context window. A million-token context window does not imply a
million-token answer. Local configuration accepts 1–128,000; the provider/model
may impose a lower output limit. Check that limit before changing models or
raising the ceiling. One million is rejected locally. Independent budget output
caps can reduce the effective ceiling and are not bypassed by this setting.

Ingestion contains several model stages, including context, challenges and Skill
candidates. Total pipeline token usage sums multiple calls and can exceed one
call’s output ceiling; it is not a provider bill. Input/output token counts and
provider pricing determine actual costs. A higher configured ceiling can also
increase budget reservations or allow longer, more expensive answers.

If import reports `ctx2skill: incomplete response (length)`, the provider marked
that answer as truncated. Check the effective output and budget caps, choose a
supported larger value, restart, and explicitly retry once. Do not treat the
truncated answer as a valid Skill. Provider limits or very long responses can
still cause truncation at 16,384; automatic retries are not enabled by this change.

## Web workflow

The API also reads `LLM_TIMEOUT` (default **120 seconds**, accepted range 1–600)
for each provider request. Set `$env:LLM_TIMEOUT = "120"` in PowerShell or
`export LLM_TIMEOUT=120` in Bash before starting; the launcher local config can
contain the same setting. Restart after changes. Multi-stage import can take
longer overall. A timeout does not imply zero provider usage; inspect records
before explicitly retrying. Increasing output capacity alone does not increase
the waiting time. For a local smoke check, try each source type separately:
Trajectory expects recorded task steps, Document expects prose instructions,
API Doc expects endpoint documentation, Script expects source code, and Past
Skills expects an existing skill description or JSON. Each parse produces an
editable candidate; it does not automatically verify or release it.

The quick start uses <http://127.0.0.1:5174/wiki>. The launcher prints its actual
URL if it selects a different free port. Keep one selected Skill ID and version
through each step; pages can carry the ID in `?skill_id=...`.

| Page | What to do | What to check |
|---|---|---|
| Knowledge Import `/ingest` | Choose the source type, enter material, review/edit the candidate, audit and create it; move to Draft when appropriate | Creation yields a candidate identity, not task-success evidence |
| Skill Wiki `/wiki` | Filter/search the library, open details, inspect schemas, implementation, provenance and evaluation | Record the exact Skill ID, version and state |
| Harness Verification `/harness` | Select an S2 Draft and **Local**; choose attempt count, repair and S3 promotion explicitly; run verification | Inspect each attempt, verifier outcome and saved evidence; promotion is separate from S4 release |
| Lifecycle `/lifecycle` | Inspect state and use the available review/transition actions | Backend qualification rules still apply; a rejected request does not create a release |
| Version Control `/versions` | Create a version, commit a snapshot, compare snapshots, or create a recovery draft | A new version/recovery has a separate S2 identity; verify that identity again |
| Agent Execution `/execution` | Run a task using available released Skills, then inspect its execution and verification details | Completion and task correctness are separate fields |
| Knowledge Graph `/graph` | Switch between Skill-only, Provenance and Version impact; select a node/edge, inspect details, open the Skill or expand related nodes | Relations and event details come from stored repository records and receipts |
| Dashboard `/`, Evolution `/evolution` | Inspect health, recorded usage and maintenance proposals | Available observations depend on the configured store and its saved runs |
| Evaluation `/evaluation`, Self-Evolution Demo `/demo` | Inspect the records or run the explicit demonstration controls | Historical/imported/demo records are distinct from fresh experiment results |

For a bounded first run, set **Local**, **Max Attempts = 1**, **Allow repair and
retry = off**, and **Promote to S3 on pass = off**. These are explicit walkthrough
settings, not a statement about every page's defaults. The web form uses the
Skill's saved evaluation cases; the CLI also accepts a separate test-case file.
External adapters require their own environments and never serve as a silent
fallback for Local.

The existing web Release action uses `/lifecycle/{id}/release` and the server's
stored qualification checks. The CLI's `lifecycle release` submits a complete
evidence bundle to `/release-with-evidence`. The web interface does not provide
an editor for every research manifest or evidence-bundle field. Use the native
API/CLI for those contracts; the two release requests are not interchangeable.

## Retrieval and the Evaluation dashboard

The planner's SkillNet-Fabric retrieval core is included in the installed SkillWiki
package, pinned to the commit and MIT license in
`skillwiki/skillwiki/_vendor/skillfabric/PROVENANCE.json`. No historical experiment
directory or separate agent CLI is needed. Its default embedding channel is local
and deterministic; remote embeddings require explicit configuration and budget
authorization. SkillWiki's planner and runtime remain responsible for execution.

The Evaluation page reads saved files; starting the server does not run benchmarks.
From the repository root, prepare its local checks in a **new** directory:

```powershell
python skillwiki/scripts/prepare_evaluation_dashboard.py --output-dir skillwiki-launcher/runtime/evaluation-results
$env:SKILLWIKI_EVALUATION_RESULTS_DIR = (Resolve-Path skillwiki-launcher/runtime/evaluation-results).Path
./skillwiki-launcher/scripts/Start-SkillWikiDemo.ps1 -NoOpen
```

For later launches, save that environment variable in the ignored
`skillwiki-launcher/config.local.ps1`. Existing services need a restart. The helper
makes **zero model calls** and runs the existing deterministic demo fixtures,
local search checks and fallback planner checks. The page labels their scope;
these figures are separate from paper results and do not measure fresh LLM quality
or live browser actions. An existing/imported results directory can be selected
with the same variable; keep its provenance and do not overwrite it. In Docker,
mount the results directory and set the variable to its container path.

Harness links resolve the exact `skill_id`, including recovery drafts and older
versions. The latest verification panel belongs to the selected identity. An
invalid ID produces an error rather than selecting a different Skill.

## Reading the graph

- **Skill-only** shows stored Skill versions and their actual dependency,
  composition and lineage relations. Multiple versions may appear together.
- **Provenance** includes typed Source, Skill, Version, Execution and Validation
  nodes when corresponding records exist. Import/audit events describe candidate
  production, not a successful task execution.
- **Version impact** projects relations through stored version, source and
  validation paths. Shared-source/discovery links are navigation hints, not
  execution dependencies. Relation scores are not calibrated probabilities.

Inspect the canonical `skill_id`, version, receipt reference, recorded outcome
and artifact binding in the details panel. Missing or mismatched artifact binding
does not prove the current Skill passed. Descendants do not inherit a parent's
verification. Refresh rebuilds the projection from the durable records; an empty
graph can be correct for an empty store. The graph itself never grants release
qualification.

## CLI workflow

Run these commands in another terminal with the backend environment active.
Place global options before the subcommand:

```bash
skillwiki --help
skillwiki --api-url http://127.0.0.1:8001 skill list
skillwiki skill search "extract structured records" --mode lexical
```

The default CLI server URL is `http://127.0.0.1:8001`. It is distinct from the
provider URL. Substitute real IDs for `SKILL_ID` / `EXECUTION_ID` and real Git
references for `COMMIT`. Commands below assume the repository root as working
directory. Request examples are in [user-guide-examples](user-guide-examples/).

### Parse, review and create

```bash
skillwiki ingest run document ./user-guide-examples/source.md
skillwiki ingest audit-candidate --file ./user-guide-examples/candidate.json
skillwiki ingest create-candidate --file ./user-guide-examples/candidate.json
skillwiki ingest status SKILL_ID
skillwiki promote SKILL_ID S2
skillwiki skill get SKILL_ID --full
skillwiki audit SKILL_ID --json
```

Parsing returns units for review. `candidate.json` is a manually authored request
example; it is not the automatic output of the preceding parse command and is
not a paper candidate. For your own material, bind the edited candidate to its
real source and task. `ingest run ... --create` is the explicit parse-and-create
alternative. `audit` checks a draft without persisting proof and may invoke the
server-configured model; it does not qualify the stored identity for release.

### Verify, execute and inspect

```bash
skillwiki verify SKILL_ID --harness local_skillwiki --max-attempts 1 --test-cases ./user-guide-examples/cases.json --json
skillwiki skill status SKILL_ID
# Run only after the Skill satisfies the server's execution-state requirements.
skillwiki skill exec SKILL_ID --input '{}'
skillwiki execution history
skillwiki execution get EXECUTION_ID
skillwiki execution trace EXECUTION_ID
skillwiki execution experience EXECUTION_ID
```

Verification defaults to one total attempt, no repair and no promotion. Explicit
opt-ins are `--allow-repair`, `--max-attempts 3`, and `--promote`; none bypass
qualification. The example case checks a JSON field's existence, not a general
quality claim. Design stronger postconditions for your actual task. The example
candidate uses a prompt and therefore requires a model for execution.

### Version, snapshot, release and recover

```bash
skillwiki lifecycle new-version SKILL_ID --file ./user-guide-examples/version.json
skillwiki skill versions SKILL_ID
skillwiki lifecycle snapshot SKILL_ID --file ./user-guide-examples/snapshot.json
skillwiki lifecycle history SKILL_ID
skillwiki lifecycle diff SKILL_ID --from-ref COMMIT --to-ref HEAD
skillwiki lifecycle release SKILL_ID --file release-evidence.json
skillwiki lifecycle rollback SKILL_ID --file recovery.json
```

Initialize the governance repository before using it (`git init ./governance`)
and start the server with `--governance-repo ./governance`, as in the quick start.
`version.json` changes the description only; include reviewed `interface` and/or
`implementation` fields for a behavior update. New-version and rollback create
separate drafts, preserve the old records, and require new applicable evidence.
Rollback does not silently replace the active released Skill.

Create `recovery.json` with a real snapshot commit:

```json
{"source_ref":"COMMIT","source_version":"1.0.0","reason":"Review recovery draft"}
```

`release-evidence.json` must be a complete native `EvidenceReleaseRequest` with
`schema_version`, `manifest`, `traces`, `evaluation`, and optional `author`.
Build it from real identity-bound evidence; use the running server's
<http://127.0.0.1:8001/docs> for the schema. No fabricated passing release file
is supplied. Submission can legitimately be rejected. `promote` is a manual
transition subject to server gates, not a substitute for evidence-bound release.

### Graph and maintenance commands

```bash
skillwiki graph show SKILL_ID --view provenance --json
skillwiki graph neighbors SKILL_ID --depth 2
skillwiki graph deps SKILL_ID
skillwiki graph export SKILL_ID --view version_impact -o graph.json
skillwiki health --json
skillwiki health SKILL_ID --json
skillwiki proposal list --json
skillwiki repair SKILL_ID
skillwiki proposal accept PROPOSAL_ID
skillwiki proposal reject PROPOSAL_ID
skillwiki evolve --json
skillwiki run "Describe the task to perform using available released Skills" --verbose
```

Choose accept or reject for a proposal; do not run both as a batch. Repair and
evolution are explicit actions that can create proposals/candidates and consume
model calls. They do not guarantee a successful repair or publication.

## Storage, errors and troubleshooting

Source checks ship with pinned AREX-Skill assets inside the Python package;
the upstream commit, Apache-2.0 license and byte hashes are recorded in
`skillwiki/skillwiki/_vendor/arex_skill/PROVENANCE.json`. No historical developer
checkout is required. Checking a source still requires its repository and Git.

A complete recovery walkthrough is: snapshot, create a recovery draft, verify
the new ID, release it when qualified, execute it, and read back the saved trace.
Passing verification does not bypass the release audit. Simulation/fixture Skills
may return `success` without interacting with a browser; use a reviewed concrete
Skill for an application demo. A request-builder Skill constructs request JSON
and does not fetch live weather from a remote service.

| Symptom | Action |
|---|---|
| Empty library/graph | Check the configured Git storage directory. New stores are empty by default; restart with the same directory to reuse saved data |
| Page loads but API fails | Check backend `/health`, then frontend `/api/v1/skills?limit=1`; set `SKILLOS_API_TARGET` **before** starting Vite |
| Port already occupied | Manual startup uses `--strictPort`; choose free ports and update the proxy/CLI URL. The launcher selects free ports and prints them |
| Release is rejected | Read the server's reason and check the exact ID/version and applicable proof; creating a candidate or snapshot alone is insufficient |
| HTTP timeout | Inspect execution history or the Harness loop before resubmitting, since the operation may have completed |
| Graph looks out of date | Refresh and inspect the receipt/identity binding; switching stores changes the visible records |
| Vite fails on startup | Check the Node version against `^20.19.0 || >=22.12.0`, then use `npm ci` |

The CLI never retries HTTP requests. `--http-timeout` is the global HTTP deadline
(600 seconds by default); verification also has an attempt `--timeout`. Any
non-passing verification returns exit code 1. `--max-retries` is a legacy alias
for the **total** `--max-attempts`, not additional retries.

For manual startup, data live under the chosen `--storage-dir` and governance
repository. The Windows launcher uses `skillwiki-launcher/runtime/skill-storage-git`
and `governance-git`. Execution history is under the configured store's
`runtime-traces/execution-history`; Harness loops are stored separately under
`artifacts/harness-runs` at the repository root. Preserve the relevant stores and receipts
together when backing up. The restore-demo script adds demonstration fixtures
and may execute verification; normal persisted data do not need that script.

## Product checks and paper reproduction

The [frontend guide](skillwiki-frontend/README.md#checks) and backend CLI contract
tests describe local checks. Some historical tests require research-workspace
fixtures that are not part of this source export. The product snapshot and
follow-up integration are recorded in [source provenance](integration-provenance/README.md).

This guide reproduces product operations. It does not deliver the paper's full
frozen experiment package. Verifying the reported tables requires the original
inputs, candidate identities, model responses, scorers and analysis scripts;
rerunning models produces a new experiment. Demo fixtures and historical
evaluation cards must not be presented as fresh paper runs.
