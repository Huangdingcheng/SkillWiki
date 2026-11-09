"""Opt-in validation of API request bodies against a pinned OpenAPI source."""
from __future__ import annotations

import hashlib
import json
import copy
import math
from typing import Any

from jsonschema import Draft202012Validator

from ..input_knowledge.api_source_rules import END, START, _document, _deref, source_contract
from .input_contract import InputContractError, validate_input_schema


class RequestBodyValidationError(InputContractError):
    def __init__(self, message: str, evidence: dict | None = None):
        super().__init__(message)
        self.execution_evidence = copy.deepcopy(evidence or {})


def compile_request_body_policy(policy: dict[str, Any]) -> dict[str, Any]:
    required = {"profile", "source_document", "source_sha256", "operation"}
    if set(policy) != required or policy.get("profile") != "openapi-json-request-v1":
        raise RequestBodyValidationError("invalid_request_body_validation_policy")
    raw = policy["source_document"]
    if not isinstance(raw, str):
        raise RequestBodyValidationError("source_document_must_be_text")
    raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    if raw_hash != policy["source_sha256"]:
        raise RequestBodyValidationError("source_identity_mismatch")
    operation = policy["operation"]
    if not isinstance(operation, dict) or set(operation) != {"path", "method"}:
        raise RequestBodyValidationError("invalid_source_operation")
    try:
        contract = source_contract({"source_operation": operation}, raw)
        document = _document(raw)
        if not str(document.get('openapi', '')).startswith('3.0.'):
            raise ValueError('unsupported_openapi_dialect')
        body, _ = _deref(document['paths'][operation['path']][operation['method'].lower()]['requestBody'], document)
        body_required = body.get('required', False)
        if type(body_required) is not bool:
            raise ValueError('request_body_required_must_be_boolean')
    except Exception as exc:
        raise RequestBodyValidationError(f"source_contract_invalid:{exc}") from exc
    schema = contract["schema"]
    Draft202012Validator.check_schema(schema)
    supported = {"type", "properties", "required", "items", "enum", "format",
                 "additionalProperties", "description", "example", "xml"}
    def check(node: Any) -> None:
        if isinstance(node, dict):
            unsupported = set(node) - supported
            if unsupported:
                raise RequestBodyValidationError(
                    "unsupported_schema_keywords:" + ",".join(sorted(unsupported))
                )
            if node.get('type') is not None and node['type'] not in ('object', 'array', 'string', 'integer', 'number', 'boolean'):
                raise RequestBodyValidationError('unsupported_schema_type')
            if node.get("format") is not None and (node.get('type') != 'integer' or node['format'] not in ('int32', 'int64')):
                raise RequestBodyValidationError("unsupported_schema_format:" + str(node["format"]))
            for child in node.get("properties", {}).values():
                check(child)
            if 'items' in node:
                check(node["items"])
            if isinstance(node.get("additionalProperties"), dict):
                check(node["additionalProperties"])
        else:
            raise RequestBodyValidationError('unsupported_non_object_schema')
    check(schema)
    Draft202012Validator.check_schema(schema)
    return {"source_sha256": raw_hash, "operation": contract["operation"],
            "media_type": contract["media_type"], "schema_pointer": contract["schema_pointer"],
            "request_body_required": body_required,
            "schema": schema, "schema_sha256": hashlib.sha256(json.dumps(
                schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest(), "validator": Draft202012Validator(schema)}


def _escape_pointer(token: Any) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def request_body_issues(body: Any, compiled: dict[str, Any], *, body_present: bool = True) -> list[dict[str, str]]:
    if not body_present:
        return ([{"path": "/body", "code": "required"}]
                if compiled["request_body_required"] else [])
    errors = []
    raw_errors = list(compiled['validator'].iter_errors(body))
    type_paths = [tuple(e.absolute_path) for e in raw_errors if e.validator == 'type']
    for error in raw_errors:
        parts = tuple(error.absolute_path)
        if error.validator != 'type' and any(parts[:len(p)] == p for p in type_paths):
            continue
        path = "/body" + "".join("/" + _escape_pointer(part) for part in error.absolute_path)
        code = "required" if error.validator == "required" else (
            "additional" if error.validator == "additionalProperties" else
            "format" if error.validator == "format" else
            "enum" if error.validator == "enum" else "type"
        )
        if error.validator == "required":
            missing = sorted(set(error.validator_value) - set(error.instance))
            errors.extend({"path": path + "/" + _escape_pointer(key), "code": "required"}
                          for key in missing)
        elif error.validator == 'additionalProperties':
            errors.extend({'path': path + '/' + _escape_pointer(k), 'code': 'additional'}
                          for k in set(error.instance) - set(error.schema.get('properties', {})))
        else:
            errors.append({"path": path, "code": code})
    def check_integer_formats(value: Any, schema: Any, path: str = "/body") -> None:
        if not isinstance(schema, dict):
            return
        if schema.get("type") == "integer" and schema.get("format") in {"int32", "int64"}:
            numeric = type(value) in (int, float) and (type(value) is int or math.isfinite(value))
            if numeric and int(value) == value:
                bits = int(schema["format"][3:])
                if not -(2 ** (bits - 1)) <= value < 2 ** (bits - 1):
                    errors.append({"path": path, "code": "format"})
        if isinstance(value, dict):
            for key, child_schema in schema.get("properties", {}).items():
                if key in value:
                    check_integer_formats(value[key], child_schema, path + "/" + _escape_pointer(key))
            if schema.get("additionalProperties") is not False:
                extra_schema = schema.get("additionalProperties")
                if isinstance(extra_schema, dict):
                    for key in set(value) - set(schema.get("properties", {})):
                        check_integer_formats(value[key], extra_schema, path + "/" + _escape_pointer(key))
        elif isinstance(value, list) and isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                check_integer_formats(item, schema["items"], path + "/" + str(index))
    check_integer_formats(body, compiled["schema"])
    return [{'path': p, 'code': c} for p, c in sorted({(e['path'], e['code']) for e in errors})]


def _strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate_json_key')
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError('nonfinite_json_number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject_constant)


def prepare_request_body_validation(skill, input_data):
    policy = getattr(skill.implementation, 'request_body_validation', None)
    if policy is None:
        return None
    if not skill.implementation.prompt_template:
        raise RequestBodyValidationError('request_body_validation_requires_prompt')
    compiled = compile_request_body_policy(policy)
    prompt = skill.implementation.prompt_template
    if START in prompt or END in prompt:
        try:
            if prompt.count(START) != 1 or prompt.count(END) != 1:
                raise ValueError('ambiguous_source_block')
            block = _strict_json(prompt.split(START, 1)[1].split(END, 1)[0])
            for key in ('source_sha256', 'operation', 'media_type', 'schema_pointer', 'schema'):
                if block[key] != compiled[key]:
                    raise ValueError('candidate_source_binding_mismatch:' + key)
        except Exception as exc:
            raise RequestBodyValidationError(str(exc)) from exc
    task = input_data.get('task_data')
    if not isinstance(task, dict) or task.get('kind') not in ('request', 'response'):
        raise RequestBodyValidationError('invalid_domain_task_kind')
    domain = task.get('input')
    if not isinstance(domain, dict):
        raise RequestBodyValidationError('invalid_domain_input')
    try:
        json.dumps(domain, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise RequestBodyValidationError('non_json_domain_input') from exc
    kind = task['kind']
    errors = request_body_issues(domain.get('body'), compiled, body_present='body' in domain) if kind == 'request' else []
    return {'profile': policy['profile'], 'source_sha256': compiled['source_sha256'],
            'schema_sha256': compiled['schema_sha256'], 'operation': compiled['operation'],
            'schema_pointer': compiled['schema_pointer'], 'kind': kind,
            'status': ('invalid' if errors else 'valid') if kind == 'request' else 'not_applicable',
            'body_present': 'body' in domain, 'issues': errors, 'applied': False}


def finalize_request_body_validation(skill, input_data, raw, evidence):
    details = {'model_raw_text': raw, 'request_body_validation': copy.deepcopy(evidence)}
    try:
        parsed = _strict_json(raw)
        details['model_output'] = copy.deepcopy(parsed)
        validate_input_schema(skill.interface.output_schema, parsed)
        if not isinstance(parsed, dict) or set(parsed) != {'case_id', 'decision', 'issues', 'request', 'response'}:
            raise ValueError('invalid_api_output_shape')
        expected_id = input_data.get('case_id', input_data['task_data'].get('case_id'))
        if parsed['case_id'] != expected_id:
            raise ValueError('output_case_identity_mismatch')
        issues = parsed['issues']
        if not isinstance(issues, list) or any(not isinstance(x, dict) or set(x) != {'path', 'code'}
                or not isinstance(x['path'], str) or not x['path'].startswith('/')
                or x['code'] not in ('required', 'type', 'format', 'enum', 'additional', 'authentication') for x in issues):
            raise ValueError('invalid_issue_shape')
        decision = parsed['decision']
        if evidence['kind'] == 'request':
            if decision not in ('send', 'reject') or parsed['response'] is not None:
                raise ValueError('invalid_request_mode_output')
            if decision == 'send':
                req = parsed['request']
                if (not isinstance(req, dict) or set(req) != {'method', 'url', 'headers', 'body'}
                    or not isinstance(req['method'], str) or not isinstance(req['url'], str)
                    or not isinstance(req['headers'], dict)
                    or any(not isinstance(k, str) or not isinstance(v, str) for k, v in req['headers'].items())):
                    raise ValueError('invalid_request_output_shape')
            elif parsed['request'] is not None:
                raise ValueError('reject_request_must_be_null')
        elif decision != 'report' or parsed['request'] is not None or not isinstance(parsed['response'], dict):
            raise ValueError('invalid_response_mode_output')
        final = copy.deepcopy(parsed)
        if evidence['kind'] == 'request' and evidence['issues']:
            retained = [x for x in issues if not (x['path'] == '/body' or x['path'].startswith('/body/'))]
            combined = {(x['path'], x['code']) for x in retained + evidence['issues']}
            final.update(decision='reject', request=None, response=None,
                         issues=[{'path': p, 'code': c} for p, c in sorted(combined)])
            details['request_body_validation']['applied'] = True
        details['request_body_validation']['changed'] = final != parsed
        return final, details
    except Exception as exc:
        raise RequestBodyValidationError('model_output_not_finalizable:' + str(exc), details) from exc
