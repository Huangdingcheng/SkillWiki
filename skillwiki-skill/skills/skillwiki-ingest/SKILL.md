---
name: skillwiki-ingest
description: Import source material and review candidates through the native SkillWiki CLI.
version: 0.3.0
---

# Import and review source material

Use an already running backend. If startup is needed, follow the repository
`USER_GUIDE.md`; Git persistence is the default. Configure model credentials in
the environment or a private local file. Do not print or commit credentials.

| Material | Source type |
|---|---|
| Operation or conversation traces | `trajectory` |
| Guides or specifications | `document` |
| API documentation | `api_doc` |
| Automation scripts | `script` |
| Existing JSON/JSONL skill definitions | `past_skills` |

```bash
skillwiki ingest run api_doc ./source.md
skillwiki ingest run api_doc ./source.md --create
skillwiki ingest run past_skills ./skills.jsonl --max-candidates 20 --create
skillwiki audit --candidate-file ./candidate.json --json
```

Model-backed extraction and review may incur charges. Without `--create`, inspect
the extraction result; `--create` requests candidate creation through the server.
Audit is candidate review, not execution proof, and does not persist the reviewed
candidate. Use `ingest create-candidate --help` for the separate persistence operation.
Audit an existing prompt skill with `skillwiki audit <skill_id>`; stored code
implementations use native verification to preserve their implementation.

Resolve review failures before release. Use `skillwiki verify` to collect native
execution evidence and the lifecycle release workflow for authorized publication.
Preserve the source identity, skill ID/version and receipts throughout. Do not
advance S3/S4 manually as a substitute for verification or release evidence.

See `user-guide-examples/` and `USER_GUIDE.md` for complete request examples.
