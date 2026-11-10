# Validation and evaluation

## Portable offline checks

Install the backend and test dependencies:

```bash
python -m pip install -e "./skillwiki[api,dev]"
python -m pytest skillwiki/tests/test_cli_contract.py skillwiki/tests/test_report_skillsbench_mapping.py skillwiki/tests/test_portable_configuration.py -o addopts= -q
python public-experiments/verify.py
```

CLI contracts use isolated FastAPI stores and block model transport. Configuration
tests check store isolation and explicit optional integration settings. The public
experiment verifier checks the packaged records and counts without model calls.

For the frontend:

```bash
cd skillwiki-frontend
npm ci
npm run lint
npm run build
```

The GitHub workflow runs these checks and builds the API/frontend container images.
Checks run in temporary stores; they do not consume or publish your skill assets.

## Evidence index

| Location | Purpose |
|---|---|
| [Public evaluation](EXPERIMENTS.md) | Materials, methods, results and interpretation |
| [Evaluation companion](public-experiments/README.md) | Synthetic inputs, expected answers, sources and revision-specific tables |
| `user-guide-examples/` | Small authored request examples for learning the current API |
| `skillwiki/benchmarks/` and benchmark-related `scripts/` | Individual benchmark adapters and report utilities; consult each entrypoint's `--help` and required inputs |
| `skillwiki/tests/` | Product regressions; historical research cases may additionally require their archived fixture package |
| `skillwiki/skillwiki/storage/skill_repo/SkillStorage/` | Historical demonstration samples, loaded only when explicitly selected |

The curated checks above are self-contained. A broad historical test run may
require archived research fixtures or pinned external framework sources; use its
fixture requirements rather than substituting generated data. Report-tool output
defaults to a relative `artifacts/` directory. Historical adapters and sample
metrics are separate from the final paper evaluation tables.

Optional SkillNet-Fabric adapter tests require the pinned source at
`artifacts/20260927-content-vertical-slice/SkillNet-REF-02/source/src`.
Without that snapshot, the three retrieval/provider tests report a missing-source
error; they do not belong to the self-contained command above.
