---
name: skillwiki-manage
description: Inspect and operate SkillWiki skills, provenance, execution records and governed versions with the native CLI.
version: 0.3.0
---

# Manage SkillWiki assets

## Connect and start

```bash
git init ./governance
skillwiki serve --backend git --storage-dir ./skillwiki-data --governance-repo ./governance --llm-retries 0
```

Set `LLM_API_KEY`, `LLM_API_URL` and `LLM_MODEL` in the server environment first.
Git is the persistent default; `memory` is an explicit temporary backend. Use
`skillwiki --api-url http://127.0.0.1:8001 <command>` for connection overrides.

## Inspect and execute

```bash
skillwiki skill list
skillwiki skill list --state S4
skillwiki skill get <skill_id> --full
skillwiki skill status <skill_id>
skillwiki skill exec <skill_id> --input '{"key":"value"}'
skillwiki health <skill_id> --json
skillwiki proposal list --status pending
skillwiki graph show <skill_id> --view provenance
skillwiki graph export <skill_id> -o graph.json --view version_impact
```

Inputs must match the selected skill's schema. Model-backed execution, proposal
generation and repair may incur charges; inspect existing records before retrying.
Graph relationships describe saved identities and evidence, not inferred success.

## Govern versions

```bash
skillwiki ingest --help
skillwiki lifecycle --help
skillwiki lifecycle snapshot --help
skillwiki execution --help
```

Use the native new-version and rollback commands with explicit request files.
New versions and recovery drafts have new identities and require their own
applicable proof. Verification and release are separate actions; publish only
when authorized and all required evidence is complete. S2 is draft, S3 is verified,
and S4 records a released asset. Keep a failed or deferred state intact and explain
its returned reason. An accepted maintenance proposal does not itself establish
qualification or release. See `USER_GUIDE.md` for payloads and command examples.
