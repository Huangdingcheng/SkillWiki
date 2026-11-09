import json

import pytest

from skillwiki.layers.skill_governance.source_update_scope import (
    RESPONSE_END,
    RESPONSE_START,
    SourceUpdateScopeError,
    response_scope_contract,
    source_update_scope,
    validate_response_scope,
    prepare_source_update_context,
    bind_source_update_proposal,
)
from skillwiki.layers.input_knowledge.api_source_rules import SourceMappingError


def _text(name: str) -> str:
    # Small standalone fixture; tests must also run without experiment archives.
    schema = {"type": "object", "required": ["name", "photoUrls"], "properties": {
        "name": {"type": "string"}, "photoUrls": {"type": "array", "items": {"type": "string"}},
        "status": {"type": "string", "enum": ["available", "pending", "sold"]}}}
    request = json.loads(json.dumps(schema))
    if name == "U04-new.json":
        request["required"].append("status")
    return json.dumps({"openapi": "3.0.3", "paths": {"/pet": {"post": {
        "requestBody": {"content": {"application/json": {"schema": request}}},
        "responses": {"200": {"description": "Successful operation", "content": {
            "application/json": {"schema": schema}}}, "400": {"description": "Invalid input"}}}}}})


def test_request_only_source_change_keeps_response_contract_equal() -> None:
    scope = source_update_scope(_text("P-old.json"), _text("U04-new.json"), {"path": "/pet", "method": "post"})
    assert scope["response_unchanged"] is True
    assert scope["scope_rule"] == "request_only"
    assert scope["old_response"] == scope["new_response"]


def test_response_change_is_detected_without_operation_specific_logic() -> None:
    old = json.loads(_text("P-old.json"))
    new = json.loads(_text("U04-new.json"))
    new["paths"]["/pet"]["post"]["responses"]["200"]["description"] = "changed"
    scope = source_update_scope(json.dumps(old), json.dumps(new), {"path": "/pet", "method": "post"})
    assert scope["response_unchanged"] is False
    assert scope["scope_rule"] == "request_and_response"


def test_complete_response_scope_block_is_accepted() -> None:
    old = _text("P-old.json")
    new = _text("U04-new.json")
    contract = response_scope_contract(new, {"path": "/pet", "method": "post"})
    contract["response_effect"] = "unchanged"
    prompt = "instructions\n" + RESPONSE_START + "\n" + json.dumps(contract, ensure_ascii=False) + "\n" + RESPONSE_END
    result = validate_response_scope(prompt, old, new, {"path": "/pet", "method": "post"})
    assert result["response_unchanged"] is True


@pytest.mark.parametrize("mutation", ["missing", "wrong_effect", "wrong_hash"])
def test_response_scope_block_rejects_ambiguous_candidates(mutation: str) -> None:
    old = _text("P-old.json")
    new = _text("U04-new.json")
    contract = response_scope_contract(new, {"path": "/pet", "method": "post"})
    if mutation != "missing":
        contract["response_effect"] = "changed" if mutation == "wrong_effect" else "unchanged"
        if mutation == "wrong_hash":
            contract["response_sha256"] = "0" * 64
        prompt = RESPONSE_START + json.dumps(contract) + RESPONSE_END
    else:
        prompt = "no response contract"
    with pytest.raises(SourceUpdateScopeError):
        validate_response_scope(prompt, old, new, {"path": "/pet", "method": "post"})


def invoice_source():
    # Independent fixture: no archived experiment files or Petstore field names.
    return {"openapi": "3.0.3", "paths": {"/invoices/{id}": {"put": {
        "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Input"}}}},
        "responses": {"201": {"description": "created", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Receipt"}}}}}
    }}}, "components": {"schemas": {
        "Input": {"type": "object", "properties": {"amount": {"type": "number"}}},
        "Receipt": {"type": "object", "properties": {"lines": {"type": "array", "items": {"$ref": "#/components/schemas/Line"}}}},
        "Line": {"type": "object", "required": ["code"], "properties": {"code": {"type": "string"}}}
    }}}


def test_independent_operation_request_change_preserves_nested_response():
    old = invoice_source()
    new = json.loads(json.dumps(old))
    new["components"]["schemas"]["Input"]["required"] = ["amount"]
    scope = source_update_scope(json.dumps(old), json.dumps(new), {"path": "/invoices/{id}", "method": "PUT"})
    assert scope["response_unchanged"]
    schema = scope["new_response"]["responses"]["201"]["content"]["application/json"]["schema"]
    assert schema["properties"]["lines"]["items"]["required"] == ["code"]


def test_nested_shared_response_dependency_change_is_detected():
    old = invoice_source()
    new = json.loads(json.dumps(old))
    new["components"]["schemas"]["Line"]["required"] = []
    scope = source_update_scope(json.dumps(old), json.dumps(new), {"path": "/invoices/{id}", "method": "put"})
    assert not scope["response_unchanged"]


@pytest.mark.parametrize("ref", ["https://example.invalid/schema", "#/components/schemas/Missing", "#/components/schemas/Receipt"])
def test_unresolved_or_cyclic_response_dependencies_are_explicit_errors(ref):
    source = invoice_source()
    source["components"]["schemas"]["Line"] = {"$ref": ref}
    with pytest.raises(SourceMappingError):
        response_scope_contract(json.dumps(source), {"path": "/invoices/{id}", "method": "put"})


def test_source_reformatting_does_not_change_response_scope():
    source = invoice_source()
    scope = source_update_scope(json.dumps(source), json.dumps(source, indent=4, sort_keys=True), {"path": "/invoices/{id}", "method": "put"})
    assert scope["response_unchanged"]


def test_duplicate_json_members_are_not_silently_accepted():
    source = json.dumps(invoice_source())
    contract = response_scope_contract(source, {"path": "/invoices/{id}", "method": "put"})
    contract["response_effect"] = "unchanged"
    raw = json.dumps(contract)[:-1] + ', "response_effect": "unchanged"}'
    with pytest.raises(SourceUpdateScopeError, match="duplicate"):
        validate_response_scope(RESPONSE_START + raw + RESPONSE_END, source, source, {"path": "/invoices/{id}", "method": "put"})


def proposal_from_context(context):
    response = dict(context["response_scope"]["new_response"])
    response["response_effect"] = "unchanged" if context["response_scope"]["response_unchanged"] else "changed"
    return ("Keep independent request and response rules.\n<skillwiki-source-request-schema>"
            + json.dumps(context["request_contract"]) + "</skillwiki-source-request-schema>\n"
            + RESPONSE_START + json.dumps(response) + RESPONSE_END)


def test_preparation_binds_exact_source_text_without_reserialization():
    import hashlib
    source = json.dumps(invoice_source(), indent=4) + "\n"
    context = prepare_source_update_context(source, source, {"path": "/invoices/{id}", "method": "put"})
    assert context["request_contract"]["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()


def test_binding_changes_only_digest_and_preserves_source_entailing_rules():
    source = json.dumps(invoice_source(), indent=4)
    operation = {"path": "/invoices/{id}", "method": "put"}
    context = prepare_source_update_context(source, source, operation)
    correct = context["request_contract"]["source_sha256"]
    context["request_contract"]["source_sha256"] = "0" * 64
    prompt = proposal_from_context(context)
    result = bind_source_update_proposal(prompt, source, source, operation)
    assert result["prompt_template"] == prompt.replace("0" * 64, correct)
    assert result["request_body_validation"]["source_document"] == source
    assert len(result["deterministic_changes"]) == 1


def test_binding_never_repairs_wrong_model_schema():
    source = json.dumps(invoice_source())
    operation = {"path": "/invoices/{id}", "method": "put"}
    context = prepare_source_update_context(source, source, operation)
    context["request_contract"]["schema"]["required"] = ["invented"]
    with pytest.raises(SourceUpdateScopeError, match="request_scope_mismatch:schema"):
        bind_source_update_proposal(proposal_from_context(context), source, source, operation)


def test_response_execution_guidance_is_generic_explicit_and_idempotent():
    from skillwiki.layers.skill_governance.source_update_scope import with_response_scope_guidance
    source = json.dumps(invoice_source())
    context = prepare_source_update_context(source, source, {"path": "/invoices/{id}", "method": "put"})
    prompt = proposal_from_context(context)
    updated = with_response_scope_guidance(prompt)
    assert updated != prompt
    assert with_response_scope_guidance(updated) == updated
    assert "required" in updated and "media type" in updated
    assert "photoUrls" not in updated and "Petstore" not in updated
    assert validate_response_scope(updated, source, source, {"path": "/invoices/{id}", "method": "put"})
    with pytest.raises(SourceUpdateScopeError):
        with_response_scope_guidance("No explicit response contract")
