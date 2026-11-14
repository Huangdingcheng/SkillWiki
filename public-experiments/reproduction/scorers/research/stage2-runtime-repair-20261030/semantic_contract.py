"""Native deterministic specs derived from the frozen API oracle contract.

Independent oracle grading remains required: JSON Schema cannot generally reject
case-insensitive duplicate arbitrary extra header names or duplicate raw JSON keys.
No expected answer is placed in the runtime input.
"""
from __future__ import annotations

import re
from evidence_tools import oracle


def _header_pattern(name):
    return "^" + "".join("[" + c.lower() + c.upper() + "]" if c.isascii() and c.isalpha() else re.escape(c) for c in name) + "$"


def _issue_set(issues):
    return {"type": "array", "minItems": len(issues), "maxItems": len(issues),
            "uniqueItems": True, "items": {"enum": issues} if issues else {}}


def semantic_specs(expected, case=None, doc=None, oracle_module=None):
    """Encode frozen grader semantics using only the native JSON Schema verifier."""
    engine = oracle_module or oracle
    source = doc if doc is not None else engine.read_source()
    props = {name: {"const": expected[name]} for name in ("case_id", "decision", "response")}
    issues = _issue_set(expected["issues"])
    if case is not None and case.get("kind") == "response":
        route, method = engine.TASKS[case["task_id"]]
        responses = source["paths"][route][method]["responses"]
        data = case["input"]
        response = responses.get(str(data["status"]), responses.get("default", {}))
        schema = response.get("content", {}).get(data.get("content_type", "application/json"), {}).get("schema")
        errors = [] if schema is None else engine.schema_issues(data.get("body"), schema, source, "/body")
        if errors:
            issues = {"anyOf": [issues, _issue_set(errors)]}
    props["issues"] = issues
    request = expected["request"]
    if request is None:
        props["request"] = {"type": "null"}
    else:
        options = [request["headers"]]
        if case is not None:
            route, method = engine.TASKS[case["task_id"]]
            options = []
            for auth in engine.auth_options(case, source["paths"][route][method]):
                headers = dict(auth)
                if method == "post":
                    headers["content-type"] = "application/json"
                if method == "delete" and "api_key" in case["input"].get("headers", {}):
                    headers["api_key"] = case["input"]["headers"]["api_key"]
                options.append(headers)
        alternatives = []
        for headers in options:
            patterns = {_header_pattern(key): {"const": value} for key, value in headers.items()}
            alternatives.append({"patternProperties": patterns, "allOf": [
                {"not": {"propertyNames": {"not": {"pattern": pattern}}}} for pattern in patterns
            ]} if patterns else {})
        request_props = {key: {"const": request[key]} for key in ("method", "url", "body")}
        request_props["headers"] = {"type": "object", "additionalProperties": {"type": "string"},
                                     "anyOf": alternatives}
        props["request"] = {"type": "object", "properties": request_props,
                            "required": list(request_props), "additionalProperties": False}
    return [{"type": "json_schema", "path": "output", "runtime_output": True,
             "schema": {"type": "object", "properties": props,
                        "required": list(props), "additionalProperties": False}}]


def case_specs(case, *, doc=None, oracle_module=None):
    engine = oracle_module or oracle
    return semantic_specs(engine.expected(case, doc), case, doc, engine)
