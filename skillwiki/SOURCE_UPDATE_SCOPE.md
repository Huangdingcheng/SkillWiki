# Explicit source-update scope checking

This optional helper separates an OpenAPI operation's request rules from its
response rules during source-driven skill updates. It has no task-specific field,
operation name, expected answer, model call, retry, or publication logic.

Module: skillwiki.layers.skill_governance.source_update_scope.

1. Read old and new source text without JSON reserialization. Select the operation
   explicitly using its source path and method.
2. Call prepare_source_update_context(old_text, new_text, operation). Give the
   returned request_contract and response_scope, the full sources, and actual
   parent implementation to a single update proposal call. Preserve the skill
   interface, output contract, auth rules, and unrelated behavior.
3. Require independent request and response rules in the proposed prompt. Include
   exactly one <skillwiki-source-request-schema> block matching the request
   contract and one <skillwiki-source-response-contract> block matching
   response_scope.new_response, with response_effect equal to unchanged or
   changed according to the source comparison. A request-only change must not
   silently tighten response rules that happen to use the same field names.
4. Call bind_source_update_proposal(prompt, old_text, new_text, operation).
   A wrong model-written schema is rejected. The only permitted correction is
   binding the embedded request source digest after all other fields match;
   deterministic_changes records it. Keep the original answer unchanged.
5. Pass the validated implementation to the existing native new-version route.
   Preserve whether request validation was explicitly enabled; this helper does
   not automatically enable it. Require new identity-bound complete validation
   before evidence-bound publication, and read the persisted result back.

The scope block is source evidence for the model, not a deterministic response
answer engine. Structural acceptance does not prove that the model follows it.
Report original model answers separately from final outputs after any existing
request validation mechanism. Preserve failed proposals and complete failed
qualification sets; never pick only successful tests for release.

This is an opt-in library integration. Ordinary generation and new-version
calls do not automatically invoke it. Its repair experiment does not establish
that all existing skills or all update paths have been upgraded.

If a valid response contract is present but a candidate omits the actual checking
procedure, with_response_scope_guidance(prompt) can explicitly add a generic
status/media selection, required-key, then recursive-value checking instruction.
It preserves the source block, is idempotent, and does not modify model outputs.
Using it changes candidate content and requires a new native version and fresh
complete qualification. Credit this developer-authored instruction separately
from autonomous model proposal quality. It is not an automatic retry or fallback.

Current boundaries: OpenAPI 3 supported by the existing parser; explicit same
operation path/method; local response schema references. Cycles and external
schema references fail explicitly. Header/link indirect-reference evolution and
operation renames are not established by this helper's current validation.
Response descriptions/examples are included in its canonical comparison, so a
description-only change counts as a changed contract even without a behavioral
change. Source byte identity and canonical response-content identity are distinct.

Tests include independent invoice/receipt fixtures, nested reference changes,
invalid references, formatting invariance, duplicate keys, and exact-text source
hashes. They do not require the experiment archive or provider credentials.
