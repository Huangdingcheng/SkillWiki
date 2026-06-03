---
name: skillwiki-ingest
description: Use this skill when the user wants to "ingest a document into SkillWiki", "add a skill from a file", "import experience into the skill wiki", "create a skill candidate from a script or trajectory", "batch import past skills", or "extract skills from docs". This skill guides the full ingest → audit → promote pipeline using the skillwiki CLI.
version: 0.1.0
---

# SkillWiki Ingest

You help the user add raw experience, documents, scripts, or existing skill definitions into the SkillWiki knowledge base using the `skillwiki` CLI.

## Prerequisites

The SkillWiki backend must be running. If it is not, start it first:

```bash
cd <repo-root>/skillwiki
venv/Scripts/activate   # Windows
# or: source venv/bin/activate  (Linux/Mac)
skillwiki serve --backend memory
```

## Step 1 — Identify source type

Ask the user what they want to ingest if not already clear. Map it to one of these types:

| What the user has | SOURCE_TYPE |
|---|---|
| Operation logs, conversation traces, step-by-step records | `trajectory` |
| Tutorial, guide, specification document | `document` |
| API endpoint documentation | `api_doc` |
| Shell script or automation script | `script` |
| Existing skill JSON / JSONL files | `past_skills` |

## Step 2 — Run ingest

```bash
# From a file
skillwiki ingest run <SOURCE_TYPE> <file_path>

# From raw text (wrap in quotes)
skillwiki ingest run <SOURCE_TYPE> "<text content>"

# Auto-create S1 candidates immediately after parsing
skillwiki ingest run <SOURCE_TYPE> <file_path> --create

# Bulk import with limit
skillwiki ingest run past_skills ./skills.jsonl --max-candidates 20 --create
```

The command prints each extracted candidate with its ID and name.

## Step 3 — Audit the candidate

Run static checks (schema completeness, safety patterns, postcondition alignment):

```bash
skillwiki audit <candidate_id>
```

If audit fails, show the issues to the user and ask whether to fix the source and re-ingest, or proceed anyway.

## Step 4 — Promote to released

Move the skill through the lifecycle:

```bash
skillwiki promote <candidate_id> S2   # draft
skillwiki promote <candidate_id> S3   # verified (after verify loop)
skillwiki promote <candidate_id> S4   # released
```

Or run the verify loop to auto-promote to S3:

```bash
skillwiki verify <candidate_id> --watch
```

## Notes

- Ingest requires a live LLM connection — the pipeline calls the LLM to extract and normalize skills.
- `past_skills` type accepts `.json` (single object or array) and `.jsonl` (one object per line).
- The `--create` flag automatically creates S1 candidates; without it, candidates are only shown but not saved.
