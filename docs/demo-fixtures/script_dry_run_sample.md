# script_dry_run_analyzer

## Goal

Create a dry-run script analysis Skill. The Skill reads a shell script as text
and returns a structured analysis without executing commands.

## Required input

- `task`: user request.
- `script_context`: source script text.
- `dry_run`: must be true.
- `allowed_paths`: non-empty list of paths that the harness is allowed to read.

## Required output

Return JSON with these top-level objects:

- `result`
- `evidence`
- `validation`

`result` should include:

- `entrypoint`
- `arguments`
- `dependencies`
- `side_effects`
- `risk_notes`

## Safety rules

- Never execute package installers, shell commands, network calls, or file
  mutations during analysis.
- If `dry_run` is false, the verifier must reject the run.
- If `allowed_paths` is empty, the verifier must reject the run.
