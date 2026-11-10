# User-guide request examples

These small requests accompany the [user guide](../USER_GUIDE.md#cli-workflow).
They are manually authored interface examples, not paper experiment candidates
or prevalidated release evidence. Run commands from the repository root after
starting a separate development store. The candidate uses a prompt, so its
execution can call your configured model.

- `source.md`: material for the parse example.
- `candidate.json`: edited candidate audit/create request; creation yields S1.
- `cases.json`: one native case checking that the output contains `result`.
- `version.json`: a description-only patch; it does not repair behavior.
- `snapshot.json`: snapshot author/message request.

Use real Skill IDs returned by your server. The guide explains separate recovery
and release files: they require your actual commit or identity-bound evidence.
