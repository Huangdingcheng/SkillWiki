# Hugging Face Space: maintainer handoff

This package serves the existing UI and native SkillWiki API on one port. It
does not seed private skills, copy experiment stores, or publish a Space.
The maintainer creates and deploys the Space after reviewing this package.

## Reviewed handoff candidate

Use branch `codex/import-output-limit`, commit `50dbe7d9`, currently carried by
PR #17: `https://github.com/Huangdingcheng/SkillWiki/pull/17`. The local final
Docker image built from this commit is
`sha256:7f36beb77a733182f946178159c032bc853a00811cb1b53f8899487d4eee57b5`.
Its isolated acceptance created skill
`50170ce2-40fa-4cca-8118-409839499cf9` v1.0.0, passed
`verify_loop_83e7be55153c`, wrote Git snapshot
`d9b96c8460d8fa18303d61429c051f28d7260633`, and read the same identities back
after restart. This was an offline native smoke; it did not spend provider
budget or prove hosted network access. Hosted provider acceptance remains the
maintainer's final step below.

## 1. Prepare the reviewed source

Use the reviewed Git commit containing the deployment files. From a clean
checkout, export to a new directory outside that checkout:

~~~bash
python deploy/huggingface/prepare_space.py ../skillwiki-space-upload
cd ../skillwiki-space-upload
docker build -t skillwiki-space .
~~~

The export replaces only the exported root README with the Space card above
(Docker SDK, port 7860). It includes space-manifest.json with the source
commit and every exported file's SHA-256. It refuses an existing destination.
The package contains product source, frontend assets, examples and deployment
documentation. Local configuration, credentials, .git, node_modules, data
stores, logs and research directories are excluded. Review the manifest before
uploading; the source commit and the file hashes jointly identify the package.

The image runs as UID/GID 1000, uses Git storage, starts without demo seed data,
and forwards /api/, /ws, /health and /docs to the native API. The backend
listens only on the container loopback. The UI keeps its existing appearance.
The frontend lockfile's mirror URLs are changed only inside the build layer
to the canonical npm registry; package versions and integrity hashes remain
unchanged. Model keys are not required and must not be provided during build.

## 2. Configure the Space before starting

Create a **private Docker Space** initially. A protected Space hides source
but still exposes the running app, so it is not the initial access boundary.
This application currently has one shared mutable store and no per-user
authorization boundary. Keep the preview restricted to trusted reviewers
until a separate public-access decision is approved.

| Setting | Location | Required value or purpose |
|---|---|---|
| LLM_API_KEY | Space Secret | Provider key, entered only in Space Settings |
| SPACE_MODEL_BUDGET_CNY | Space Variable | Positive spending ceiling explicitly approved by the maintainer; no default allocation |
| LLM_API_URL | Space Variable | Default https://api.deepseek.com |
| LLM_MODEL | Space Variable | Default deepseek-flash; the current budget guard prices this route and rejects unpriced routes |
| LLM_MAX_TOKENS | Space Variable | Default 16384 output tokens per model call, within the provider's output limit |
| LLM_TIMEOUT | Space Variable | Default 120 seconds per provider call; Nginx allows 600 seconds between upstream reads |
| SPACE_DATA_DIR | Space Variable | Default /data; must be an absolute writable filesystem directory |

Automatic provider retries are disabled. Multi-stage imports may involve more
than one model call. A one-million-token output setting is not supported;
the provider's context capacity is not an output-token allowance. A health
check does not call the model. Missing key, invalid budget, unsupported priced
route or unwritable storage causes startup to fail instead of presenting a
healthy but unusable app.

Use only the native local_skillwiki execution route for this Space. No Codex
CLI or Claude CLI is installed. Do not add these tools as a fallback.

## 3. Persistence is an explicit deployment choice

All mutable state is routed below SPACE_DATA_DIR:

| Subdirectory | Data |
|---|---|
| skills/ | Git-backed skills, versions, execution traces and maintenance records |
| governance/ | Git snapshots, release metadata and lifecycle records |
| receipts/ | Native harness execution/verification evidence |
| budget/ | Cumulative model-cost ledger |
| work/ | Process working files and logs |

Docker Space local disk is ephemeral unless persistent storage is explicitly
provisioned. A directory named /data alone does not make it persistent.
Choose a writable persistent filesystem volume supported by the Space setup,
then mount/map it to SPACE_DATA_DIR and verify restart/rebuild recovery before
using it for retained evidence. Bucket/object storage is not assumed to have
the locking and rename semantics needed by active Git repositories. Use it
for consistent backups unless its mounted filesystem behavior has been tested.

For an ephemeral review, export a backup of all five directories before
rebuilds and treat the usage ceiling as limited to that ledger's lifetime.
Deleting the ledger resets local accounting; provider-side spending limits
remain necessary for an externally exposed service. Never describe ephemeral
storage as durable or a reset ledger as a continuing budget cap.

## 4. Local acceptance before upload

For the offline packaging checks, a non-secret test key suffices because no
model request is made. Use a private environment with the real key and approved
budget separately for provider acceptance:

~~~bash
docker volume create skillwiki-space-review
docker run -d --name skillwiki-space-review \
  -p 127.0.0.1:17860:7860 \
  -v skillwiki-space-review:/data \
  -e LLM_API_KEY=offline-smoke-not-a-real-key \
  -e SPACE_MODEL_BUDGET_CNY=1 skillwiki-space
python deploy/huggingface/smoke.py --base-url http://127.0.0.1:17860 --output space-smoke.json
docker restart skillwiki-space-review
python deploy/huggingface/smoke.py --base-url http://127.0.0.1:17860 --readback space-smoke.json
~~~

The smoke script requires an empty isolated store, creates one deterministic
test skill, performs native execution and verification, creates a Git snapshot,
checks WebSocket routing, then re-reads that identity after restart. It does
not call a model or publish a skill. Do not run it against an existing demo
or production store. See LOCAL_VALIDATION.md for the recorded local result.

## 5. Upload and complete hosted acceptance

1. Copy the exported files into the private Space repository and push them.
2. Add the Secret and Variables in Space Settings; configure storage.
3. Confirm the build succeeds and /health, /wiki, /docs load on the Space URL.
   Confirm WebSocket connection on the same host.
4. Run one small API Doc import with the real runtime Secret, review and create
   the candidate, execute it with the documented input, then verify it. Check
   both the displayed outcome and the recorded execution identity.
5. Exercise the documented Git snapshot/version/release/restore flow under
   the maintainer's authorization, read back the saved identity, restart the
   Space, and read it again. Back up the complete store before a rebuild.
6. Record hosted image/revision, provider, cost, source and outcome. A successful
   local smoke test does not replace the hosted provider/network acceptance.

Reference: Hugging Face's [Docker Spaces guide](https://huggingface.co/docs/hub/spaces-sdks-docker)
and [Spaces overview](https://huggingface.co/docs/hub/spaces-overview).
