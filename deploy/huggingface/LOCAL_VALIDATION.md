# Local Docker Space packaging acceptance

Date: 2026-11-12. These checks use local disposable stores and synthetic data.
No provider request, external publication or Hugging Face deployment was made.
The product's separate real-provider walkthrough is recorded by the integration
handoff; it must not be inferred from these zero-model packaging tests.

## Packaging baseline

The baseline image was built from product commit
9826b00962cc6894333de880d55ee57823e44ebb plus the new packaging files.
Image: sha256:9b420d3a9d78e5cf724f5464abe8ea35b79004a9cb2f1b824f154c150df5e2b9.
This is the packaging baseline; final integrated-product revalidation is
recorded below once its reviewed source is frozen.

Installed baseline runtime: Python 3.12.16, FastAPI 0.162.5, Starlette 1.10.0,
Uvicorn 0.54.0, websockets 17.4, Pydantic 2.14.3, httpx 0.28.1, NumPy 2.5.4.
The Docker base tags and Python requirements are compatible ranges, not a
bit-for-bit environment lock. Record the new image digest and installed
dependencies whenever the maintainer rebuilds it.

| Check | Observed result |
|---|---|
| Linux image build | Exit 0; Python API installation and frontend production build complete |
| Startup regression suite | 7 tests pass; missing key/budget, invalid budget, relative data path, unpriced provider route, export exclusions and overwrite refusal |
| Missing runtime Secret | Container exits 1 with an actionable configuration message |
| Unprivileged process | UID 1000, GID 1000 |
| UI and API | /wiki, its JS asset, /health, /docs and /openapi.json return correctly |
| Same-origin WebSocket | /ws returns HTTP 101 through the single port |
| Empty initial store | GET /api/v1/skills returns an empty list before fixture creation |
| Native execution and verification | One local_skillwiki attempt echoes the supplied text and passes its deterministic verifier |
| Native Git snapshot | Snapshot commit created and read back through the lifecycle API |
| Persistent receipts | Harness root resolves to /data/receipts/harness-runs; six native JSON evidence files are saved |
| Restart | Same skill ID/version, verification loop ID and snapshot commit read back after docker restart |
| Process supervision | Terminating the required backend causes the container to exit 1 and stops the proxy |
| License | Root MIT license exists in /app/LICENSE |
| Provider usage | Zero requests; budget directory contains no usage entries |

Reusable smoke-script acceptance identity:

- Skill: 1209ee07-167c-4064-9b2b-80b2121dc7c9, version 1.0.0.
- Verification loop: verify_loop_eba8070c0c13.
- Snapshot: 063232cd363305daf2eb5c51eb6958e23eca08d0.
- Verification status: passed; final skill state S2; no publication performed.

The build uses official Debian HTTPS sources and the canonical npm registry.
Nginx temporary directories are explicitly writable by UID 1000. Initial
packaging/network failures were corrected before this acceptance. The runtime
and UI algorithms were not changed by this packaging work.

## Final integrated product

Reviewed source branch: `codex/import-output-limit` (PR #17), commit
`50dbe7d9` (`Prepare governed demo for Hugging Face Space handoff`).

The final integrated image was rebuilt from that reviewed source and passed the
isolated smoke plus restart readback:

- Image: `sha256:7f36beb77a733182f946178159c032bc853a00811cb1b53f8899487d4eee57b5`
- Smoke result: exit 0; frontend/API/WebSocket/empty-store checks passed;
  native verifier passed; no provider request was made.
- Skill identity: `50170ce2-40fa-4cca-8118-409839499cf9`, version `1.0.0`.
- Verification loop: `verify_loop_83e7be55153c`.
- Snapshot: `d9b96c8460d8fa18303d61429c051f28d7260633`.
- Restart readback: passed with the same skill, loop and snapshot identities.

The final smoke receipt is kept outside the source checkout in the private
predeploy evidence directory. The baseline above remains historical evidence;
the final image and identity above are the acceptance record for the reviewed
candidate.

## Maintainer's hosted acceptance

Hosted deployment is not performed by this task. Space Secrets, approved model
budget, actual storage persistence and private initial visibility must be set
by the maintainer. The [handoff](HANDOFF.md) lists the hosted real-provider
import, execution and authorized Git lifecycle walkthrough still required
after deployment. Anonymous public multiuser safety is not claimed.
