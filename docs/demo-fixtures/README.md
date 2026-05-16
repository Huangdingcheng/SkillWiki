# SkillOS Demo Fixtures

These fixtures are small, public, copy-pasteable inputs for the SkillOS demo.
They are intentionally safe and synthetic: no real credentials, no private API
keys, and no external downloads are required.

Use them in two ways:

- Manual UI demo: open `Knowledge Import`, pick the matching source type, then
  paste or drag the file into the input area.
- Repeatable backend demo: run `RESTORE_SKILLOS_DEMO_STATE.bat` from the
  repository root. The script imports these fixtures through the same public
  backend APIs that the UI uses.

Fixture map:

- `approved_past_skills.json`: Past Skills import examples.
- `document_ctx2skill_sample.md`: Document input for Ctx2Skill-lite evidence.
- `script_dry_run_sample.md`: Script input for dry-run analysis Skill creation.
- `script_shell_installer.sh`: shell script text used by the harness positive
  and negative checks.
- `legacy_login_past_skill.json`: Past Skills example with dependency,
  composition, and lineage relations.
- `related_login_graph_pack.json`: seven related Skills used to validate
  Skill-only graph edges, heterogeneous provenance graph, and projection view.
