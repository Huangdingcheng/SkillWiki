# SkillWiki Skill Package

This package provides three skills for Claude Code and Codex agents to interact with a SkillWiki knowledge base via the `skillwiki` CLI.

## Skills

| Skill | Trigger | What it does |
|---|---|---|
| `skillwiki-ingest` | "ingest a document", "import skills from file" | Guides the full ingest → audit → promote pipeline |
| `skillwiki-verify` | "verify a skill", "run the verify loop" | Runs execute-verify-repair loop until postconditions pass |
| `skillwiki-manage` | "list skills", "run a task", "find a skill" | General skill management, query, and execution |

## Installation

### Claude Code

Copy this folder into your project's `.claude/plugins/` directory:

```bash
cp -r skillwiki-skill /your-project/.claude/plugins/skillwiki
```

Or install globally:

```bash
cp -r skillwiki-skill ~/.claude/plugins/skillwiki
```

### Codex

Place the folder anywhere accessible and point Codex to it via your agent config.

## Requirements

- SkillWiki backend running (`skillwiki serve`)
- `skillwiki` CLI installed (`pip install -e ./skillwiki`)
- Python 3.10+

## Structure

```
skillwiki-skill/
├── .claude-plugin/
│   └── plugin.json          # plugin metadata
├── skills/
│   ├── skillwiki-ingest/
│   │   └── SKILL.md         # ingest pipeline skill
│   ├── skillwiki-verify/
│   │   └── SKILL.md         # verify loop skill
│   └── skillwiki-manage/
│       └── SKILL.md         # management & query skill
└── README.md
```
