"""Generic source-update scope contracts for request/response separation."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Mapping

from ..input_knowledge.api_source_rules import _document, _expand, _deref

RESPONSE_START = "<skillwiki-source-response-contract>"
RESPONSE_END = "</skillwiki-source-response-contract>"
RESPONSE_GUIDANCE = (
    "For a supplied response, use the independent source response contract below, not the request schema. "
    "First select the applicable declared status and media type using the task contract. "
    "If it declares a body schema, enumerate every required property and check key membership in the "
    "supplied body before inspecting present values. A missing required property makes the body invalid. "
    "Then check the types and constraints of present values, nested objects, and every array item. "
    "Return a valid body result only after both required-property and value checks pass. "
    "Do not borrow request-only required fields or enums. If no body schema is declared, do not invent "
    "one. Keep all status routing, output fields, and classifications defined by the task contract."
)


class SourceUpdateScopeError(ValueError):
    """Raised when an update candidate does not bind its response scope."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SourceUpdateScopeError("response_scope_block_duplicate_key:" + key)
        result[key] = value
    return result


def _operation(document: Mapping[str, Any], operation: Mapping[str, str]) -> Mapping[str, Any]:
    path = str(operation.get("path") or "")
    method = str(operation.get("method") or "").lower()
    if not path or not method:
        raise SourceUpdateScopeError("invalid_source_operation")
    value = document.get("paths", {}).get(path, {}).get(method)
    if not isinstance(value, Mapping):
        raise SourceUpdateScopeError("source_operation_not_found")
    return value


def _response_value(value: Any, document: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping) and "$ref" in value:
        value, _ = _deref(dict(value), dict(document))
    if isinstance(value, Mapping):
        result = {str(k): copy.deepcopy(v) for k, v in value.items() if k != "$ref"}
        if "content" in result and isinstance(result["content"], Mapping):
            content = {}
            for media, media_value in result["content"].items():
                item = copy.deepcopy(media_value)
                if isinstance(item, Mapping) and isinstance(item.get("schema"), Mapping):
                    item["schema"] = _expand(dict(item["schema"]), dict(document))
                content[str(media)] = item
            result["content"] = content
        return result
    return copy.deepcopy(value)


def response_scope_contract(source_text: str, operation: Mapping[str, str]) -> dict[str, Any]:
    """Return a canonical, source-derived contract for an operation's responses."""
    document = _document(source_text)
    op = _operation(document, operation)
    responses = op.get("responses")
    if not isinstance(responses, Mapping) or not responses:
        raise SourceUpdateScopeError("source_responses_missing")
    normalized = {str(key): _response_value(value, document)
                  for key, value in sorted(responses.items(), key=lambda item: str(item[0]))}
    contract = {
        "operation": {"path": str(operation["path"]), "method": str(operation["method"]).upper()},
        "responses": normalized,
    }
    contract["response_sha256"] = _hash(contract)
    return contract


def source_update_scope(old_source: str, new_source: str, operation: Mapping[str, str]) -> dict[str, Any]:
    """Build the update-side request/response boundary without task-specific names."""
    old_response = response_scope_contract(old_source, operation)
    new_response = response_scope_contract(new_source, operation)
    return {
        "operation": dict(operation),
        "old_response": old_response,
        "new_response": new_response,
        "response_unchanged": old_response["response_sha256"] == new_response["response_sha256"],
        "scope_rule": (
            "request_only" if old_response["response_sha256"] == new_response["response_sha256"]
            else "request_and_response"
        ),
    }


def extract_response_scope(prompt: str) -> dict[str, Any]:
    """Extract exactly one response scope block from a candidate prompt."""
    if not isinstance(prompt, str) or prompt.count(RESPONSE_START) != 1 or prompt.count(RESPONSE_END) != 1:
        raise SourceUpdateScopeError("response_scope_block_cardinality")
    content = prompt.split(RESPONSE_START, 1)[1].split(RESPONSE_END, 1)[0].strip()
    try:
        value = json.loads(content, object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc:
        raise SourceUpdateScopeError("response_scope_block_invalid_json") from exc
    if not isinstance(value, dict):
        raise SourceUpdateScopeError("response_scope_block_not_object")
    return value


def with_response_scope_guidance(prompt: str) -> str:
    """Explicit optional model procedure; no output rewriting or proof implied."""
    extract_response_scope(prompt)
    if RESPONSE_GUIDANCE in prompt:
        return prompt
    return prompt.replace(RESPONSE_START, RESPONSE_GUIDANCE + "\n" + RESPONSE_START, 1)


def validate_response_scope(prompt: str, old_source: str, new_source: str,
                           operation: Mapping[str, str]) -> dict[str, Any]:
    """Require a candidate to bind unchanged/changed response semantics to source bytes."""
    scope = source_update_scope(old_source, new_source, operation)
    actual = extract_response_scope(prompt)
    expected = scope["new_response"]
    if actual.get("response_effect") != ("unchanged" if scope["response_unchanged"] else "changed"):
        raise SourceUpdateScopeError("response_effect_does_not_match_source_diff")
    for key in ("operation", "responses", "response_sha256"):
        if actual.get(key) != expected.get(key):
            raise SourceUpdateScopeError("response_scope_does_not_match_new_source:" + key)
    return scope


def prepare_source_update_context(old_source: str, new_source: str,
                                  operation: Mapping[str, str]) -> dict[str, Any]:
    """Opt-in OpenAPI update evidence; exact source text defines its identity.

    Supports the existing local-reference OpenAPI parser. This is evidence
    preparation, not proposal generation or proof of downstream correctness.
    """
    from ..skill_runtime.request_body_validation import compile_request_body_policy
    policy = {"profile": "openapi-json-request-v1", "source_document": new_source,
              "source_sha256": hashlib.sha256(new_source.encode("utf-8")).hexdigest(),
              "operation": dict(operation)}
    request = compile_request_body_policy(policy)
    return {"response_scope": source_update_scope(old_source, new_source, operation),
            "request_contract": {key: request[key] for key in
                ("operation", "media_type", "schema_pointer", "schema", "source_sha256")},
            "request_body_validation": policy}


def bind_source_update_proposal(prompt: str, old_source: str, new_source: str,
                                operation: Mapping[str, str]) -> dict[str, Any]:
    """Validate both source scopes; bind only the request source digest.

    Never correct model-written schemas or infer behavioral qualification.
    The caller still uses native new-version and fresh complete qualification.
    """
    from ..input_knowledge.api_source_rules import START, END
    context = prepare_source_update_context(old_source, new_source, operation)
    validate_response_scope(prompt, old_source, new_source, operation)
    if prompt.count(START) != 1 or prompt.count(END) != 1:
        raise SourceUpdateScopeError("request_scope_block_cardinality")
    match = re.search(re.escape(START) + r"\s*(.*?)\s*" + re.escape(END), prompt, re.S)
    if match is None:
        raise SourceUpdateScopeError("request_scope_block_order")
    try:
        actual = json.loads(match.group(1), object_pairs_hook=_reject_duplicate_pairs)
    except json.JSONDecodeError as exc:
        raise SourceUpdateScopeError("request_scope_invalid_json") from exc
    if not isinstance(actual, dict):
        raise SourceUpdateScopeError("request_scope_not_object")
    expected = context["request_contract"]
    for key in ("operation", "media_type", "schema_pointer", "schema"):
        if actual.get(key) != expected[key]:
            raise SourceUpdateScopeError("request_scope_mismatch:" + key)
    before = actual.get("source_sha256")
    if not isinstance(before, str) or re.fullmatch(r"[0-9a-f]{64}", before) is None:
        raise SourceUpdateScopeError("request_scope_digest_invalid")
    changes = []
    if before != expected["source_sha256"]:
        replacement, count = re.subn(r'("source_sha256"\s*:\s*")[0-9a-f]{64}(")',
            lambda m: m.group(1) + expected["source_sha256"] + m.group(2), match.group(1))
        if count != 1:
            raise SourceUpdateScopeError("request_scope_digest_ambiguous")
        prompt = prompt[:match.start(1)] + replacement + prompt[match.end(1):]
        changes.append({"field": "embedded.source_sha256", "before": before,
                        "after": expected["source_sha256"],
                        "role": "deterministic identity binding, not model correctness"})
    return {"prompt_template": prompt, "request_body_validation": context["request_body_validation"],
            "response_scope": context["response_scope"], "deterministic_changes": changes}
