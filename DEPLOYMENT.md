# Deployment

The [native quick start](README.md#quick-start) provides the local CLI and web
workflow. Docker Compose packages the same backend and frontend with persistent
storage, an API health check, and an Nginx proxy.

## Docker Compose

Install Docker with Compose and start its engine. Set `LLM_API_KEY`, `LLM_API_URL`
and `LLM_MODEL` in the shell or a private root `.env` file. The `.env` file is
Git-ignored. Then run from the repository root:

```bash
docker compose config --quiet
docker compose up --build -d
docker compose ps
```

Open <http://localhost:8080/wiki>. The API is at
<http://localhost:8000/docs> and health at <http://localhost:8000/health>.
Host bindings are local by default. The frontend proxies `/api/` to the backend.
Stores start empty; use your own source or the [request examples](user-guide-examples/README.md).

| Volume | Content |
|---|---|
| `skillwiki-data` | Skill versions, runtime traces and governance Git repository |
| `skillwiki-receipts` | Harness receipts and related artifacts |

```bash
docker compose logs --tail 100 api
docker compose stop
docker compose start
```

Stopping or removing containers with `docker compose down` preserves named
volumes. Back up both volumes before replacing a deployment. `down --volumes`
deletes their contents and is not an ordinary restart command.

The API image installs Git and the API dependencies. Its entrypoint initializes
the governance repository if needed. Verification defaults remain explicit:
zero provider retries, no seeded data, and native local execution. Model-backed
operations use the configured provider and may incur charges.

## Configuration

| Setting | Purpose |
|---|---|
| `LLM_API_KEY`, `LLM_API_URL`, `LLM_MODEL` | Server model credentials, endpoint and model |
| `LLM_MAX_TOKENS` | Per-call output ceiling; API/Compose default 16384. Must fit the provider/model output limit; see [token configuration](USER_GUIDE.md#output-tokens-and-truncated-responses) |
| `LLM_TIMEOUT` | Per-provider-request timeout in seconds; API/Compose default 120, range 1–600 |

| `skillwiki serve --storage-dir` | Persistent skill store |
| `skillwiki serve --governance-repo` | Existing Git repository for snapshots and releases |
| `skillwiki serve --llm-retries` | Provider retry count; CLI default 0 |
| `SKILLWIKI_STORAGE_DIR` | Store fallback for direct Python API startup |
| `SKILLWIKI_PAPERQA_ENV_FILE` | Explicit optional remote embedding configuration file |

The bundled frontend Nginx proxy allows 600 seconds between backend reads for
multi-stage imports. If you increase the provider timeout or use another reverse
proxy, align its read timeout with the whole import duration, then restart the
affected services. A provider timeout applies to one model stage, not the entire
import pipeline.

Remote embedding additionally requires its explicit mode, authorization and budget
ledger. Ordinary startup does not read another project's configuration. The
optional `skillwiki/config.yaml` is for callers that explicitly load per-agent
configuration; it does not override the documented `serve` command. Database
examples there describe separate integrations, not additional CLI storage choices.

The [test guide](TESTING.md) lists offline checks and CI coverage. The container
build job validates Compose and builds both images; live provider calls are kept
separate from packaging checks.

## Hugging Face Docker Space

For a single-port hosted preview, use the root Dockerfile and the
[maintainer handoff](deploy/huggingface/HANDOFF.md). The package retains the
existing UI, serves UI/API/WebSocket traffic on port 7860, runs as UID 1000,
starts with an empty Git-backed store, and keeps skills, governance, receipts
and the cost ledger under one configured data root. The handoff explains
Secrets, explicit spending authorization, persistent storage and the initial
private access scope. Deployment is performed by the repository maintainer.
