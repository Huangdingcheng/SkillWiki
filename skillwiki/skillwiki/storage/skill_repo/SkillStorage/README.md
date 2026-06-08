# SkillStorage

Git-backed Skill storage repository for SkillWiki.

- `skills/{skill_name}/{version}.json`: immutable skill version payload
- `skills/{skill_name}/versions.json`: per-skill version manifest
- `metadata/skills_index.json`: query index
- `metadata/events.jsonl`: append-only lifecycle event log
