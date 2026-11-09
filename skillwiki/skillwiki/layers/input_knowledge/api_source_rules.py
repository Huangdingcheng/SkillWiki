"""Source-backed API knowledge, separate from Skill invocation schemas."""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
import re
from typing import Any
import yaml

METHODS = {'get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'}
START = '<skillwiki-source-request-schema>'
END = '</skillwiki-source-request-schema>'


class SourceMappingError(ValueError):
    pass


def _pointer(token: str) -> str:
    return token.replace('~', '~0').replace('/', '~1')


def _document(raw: str) -> dict[str, Any]:
    # Exact archived source envelope; never guess an embedded JSON fragment.
    marker = 'Frozen operation source:' + chr(10)
    document = json.loads(raw.split(marker, 1)[1]) if marker in raw else yaml.safe_load(raw)
    if not isinstance(document, dict) or not str(document.get('openapi', '')).startswith('3.'):
        raise SourceMappingError('source_is_not_openapi_3')
    return document


def _deref(value: Any, document: dict, seen: frozenset[str] = frozenset()) -> tuple[dict, frozenset[str]]:
    if not isinstance(value, dict):
        raise SourceMappingError('schema_or_request_body_is_not_object')
    ref = value.get('$ref')
    if ref is None:
        return value, seen
    if not isinstance(ref, str) or not ref.startswith('#/'):
        raise SourceMappingError('external_or_invalid_ref')
    if ref in seen:
        raise SourceMappingError('cyclic_ref')
    if len(value) != 1:
        raise SourceMappingError('ref_siblings_require_explicit_handling')
    target: Any = document
    try:
        for token in ref[2:].split('/'):
            target = target[token.replace('~1', '/').replace('~0', '~')]
    except (KeyError, TypeError):
        raise SourceMappingError('unresolved_local_ref') from None
    return _deref(target, document, seen | {ref})


def _expand(value: dict, document: dict, seen: frozenset[str] = frozenset(), depth: int = 0) -> dict:
    if depth > 48:
        raise SourceMappingError('schema_depth_limit')
    node, seen = _deref(value, document, seen)
    result = deepcopy(node)
    # Expand schema positions only; examples are preserved as source data.
    for key in ('properties', 'patternProperties', '$defs', 'definitions', 'dependentSchemas'):
        if isinstance(node.get(key), dict):
            result[key] = {name: _expand(child, document, seen, depth + 1)
                           if isinstance(child, dict) else child for name, child in node[key].items()}
    for key in ('items', 'additionalProperties', 'not', 'if', 'then', 'else', 'contains'):
        if isinstance(node.get(key), dict):
            result[key] = _expand(node[key], document, seen, depth + 1)
    for key in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
        if isinstance(node.get(key), list):
            result[key] = [_expand(child, document, seen, depth + 1)
                           if isinstance(child, dict) else child for child in node[key]]
    return result


def source_contract(candidate: dict, raw: str) -> dict:
    document = _document(raw)
    operations = []
    for path, item in document.get('paths', {}).items():
        if isinstance(item, dict):
            for method, operation in item.items():
                if method in METHODS and isinstance(operation, dict):
                    operations.append((path, method, operation))
    explicit = candidate.get('source_operation')
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) != {'path', 'method'}:
            raise SourceMappingError('invalid_explicit_operation')
        operations = [op for op in operations if op[0] == explicit['path'] and op[1] == str(explicit['method']).lower()]
        if len(operations) != 1:
            raise SourceMappingError('explicit_operation_not_found')
    if len(operations) == 1:
        path, method, operation = operations[0]
        body = operation.get('requestBody')
        origin = f'/paths/{_pointer(path)}/{method}/requestBody'
        identity = {'path': path, 'method': method.upper()}
    elif operations:
        raise SourceMappingError('ambiguous_source_operation')
    else:
        bodies = document.get('components', {}).get('requestBodies', {})
        if len(bodies) != 1:
            raise SourceMappingError('ambiguous_or_missing_request_body')
        name, body = next(iter(bodies.items()))
        origin = f'/components/requestBodies/{_pointer(name)}'
        identity = {'request_body': name}
    body, _ = _deref(body, document)
    media = body.get('content', {}).get('application/json')
    if not isinstance(media, dict) or not isinstance(media.get('schema'), dict):
        raise SourceMappingError('json_request_schema_missing')
    schema = _expand(media['schema'], document)
    if len(json.dumps(schema, ensure_ascii=False)) > 48000:
        raise SourceMappingError('source_schema_size_limit')
    return {'operation': identity, 'media_type': 'application/json',
            'source_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
            'schema_pointer': origin + '/content/application~1json/schema', 'schema': schema}


def _skip(reason: str) -> list[dict]:
    return [{'kind': 'source_domain_rules_skipped', 'reason': reason}]


def attach_source_rules(candidate: dict, raw: str) -> list[dict]:
    if candidate.get('prompt_format') != 'literal':
        return _skip('explicit_literal_prompt_required')
    prompt = candidate.get('prompt_template')
    if not isinstance(prompt, str) or not prompt.strip():
        return _skip('prompt_missing')
    try:
        contract = source_contract(candidate, raw)
    except (ValueError, TypeError, AttributeError, RecursionError, yaml.YAMLError) as exc:
        return _skip(str(exc) if isinstance(exc, SourceMappingError) else 'source_parse_error')
    block = START + chr(10) + json.dumps(contract, ensure_ascii=False, sort_keys=True) + chr(10) + END
    if START in prompt or END in prompt:
        return [] if block in prompt else _skip('existing_source_rules_conflict')
    candidate['prompt_template'] = prompt.rstrip() + chr(10) + (
        'The following source request schema is domain evidence for the identified operation and media type. '
        'When asked to validate a request, apply every nested rule to the supplied body and report violations '
        'using the task output contract. Traverse each supplied nested object and each array item before constructing a request. '
        'For a string pattern, evaluate the entire expression: account for anchors, every mandatory position, and quantifiers; '
        'matching the allowed character class alone does not establish a pattern match. '
        'Invalid domain values are objects of diagnosis. '
        'Keep the Skill invocation envelope and the task output contract unchanged. '
        'An omitted additionalProperties rule does not prohibit extra properties; preserve allowed supplied fields.'
    ) + chr(10) + block
    return [{'kind': 'source_domain_rules_attached', 'path': 'prompt_template',
             **{k: v for k, v in contract.items() if k != 'schema'}}]


def _all_extra_policies_allow(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    if any(k in schema for k in ('allOf', 'anyOf', 'oneOf', 'not', 'if', 'then', 'else',
                                  'unevaluatedProperties', 'patternProperties', 'propertyNames')):
        return False
    extra = schema.get('additionalProperties', True)
    if extra is not True and extra != {}:
        return False
    properties = schema.get('properties', {})
    if not isinstance(properties, dict):
        return False
    if not all(_all_extra_policies_allow(child) for child in properties.values()):
        return False
    return 'items' not in schema or _all_extra_policies_allow(schema['items'])


def remove_unsupported_body_ban(candidate: dict, raw: str) -> list[dict]:
    if candidate.get('prompt_format') != 'literal':
        return _skip('explicit_literal_prompt_required')
    try:
        contract = source_contract(candidate, raw)
    except (ValueError, TypeError, AttributeError, RecursionError, yaml.YAMLError) as exc:
        return _skip(str(exc) if isinstance(exc, SourceMappingError) else 'source_parse_error')
    if not _all_extra_policies_allow(contract['schema']):
        return _skip('explicit_or_complex_extra_property_policy_preserved')
    prompt = candidate.get('prompt_template', '')
    if not isinstance(prompt, str):
        return _skip('prompt_missing')
    prose, separator, evidence = prompt.partition(START)
    # Modify only the request-body validation clause, never an output/envelope ban.
    clause = r'\bValidate\b[^.\n]*\bbody\b[^.\n]*?\bno (?:undocumented )?additional properties\b'
    count = 0

    def correct_request_clause(match: re.Match) -> str:
        nonlocal count
        text = match.group(0)
        # A response/output restriction is independent of the request schema.
        if re.search(r'\b(response|output)\b', text, re.I):
            return text
        count += 1
        return re.sub(r'\bno (?:undocumented )?additional properties\b',
                      'additional properties follow the source schema', text, flags=re.I)

    edited = re.sub(clause, correct_request_clause, prose, flags=re.I)
    if not count:
        return _skip('no_unambiguous_request_body_ban_clause')
    candidate['prompt_template'] = edited + separator + evidence
    return [{'kind': 'removed_unsupported_constraint', 'path': 'prompt_template',
             **{k: v for k, v in contract.items() if k != 'schema'}}]
