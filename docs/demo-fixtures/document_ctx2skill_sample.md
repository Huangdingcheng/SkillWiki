# document_grounded_extractor

## Context

A reusable document-grounded extraction skill should transform a long technical
document into a structured summary that can be checked by a deterministic
verifier. The input document may contain procedures, constraints, examples,
tool descriptions, and expected outputs.

## Required behavior

1. Read the source document without mutating it.
2. Separate the content into facts, procedures, constraints, examples, and
   tools or APIs.
3. Generate a short task challenge that asks whether the extracted structure is
   sufficient to reproduce the procedure.
4. Return JSON with `result`, `evidence`, and `validation` objects.
5. Include at least one evidence item that points back to the source context.

## Constraints

- Do not invent tool parameters that are not present in the document.
- If a required field is missing, report it in `validation.missing_fields`.
- The extractor is a candidate Skill until a harness or human reviewer verifies
  it.

## Example challenge

Task: Given a deployment guide, extract the command sequence, required
environment variables, expected success signal, and failure signal.

Rubric:

- The output must contain structured steps.
- Required environment variables must be listed separately.
- The success signal and failure signal must be explicit.
