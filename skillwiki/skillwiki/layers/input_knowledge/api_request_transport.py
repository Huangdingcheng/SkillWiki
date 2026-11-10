"""Opt-in source facts and explicit task bindings for request construction.

This does not construct requests, inject credentials, or alter model output.
No default pipeline calls this helper.
"""
from copy import deepcopy
import hashlib
import json
from .api_source_rules import METHODS, SourceMappingError, _document, _deref, _pointer

START = '<skillwiki-source-request-transport>'
END = '</skillwiki-source-request-transport>'


def transport_contract(candidate: dict, raw: str, binding: dict) -> dict:
    document = _document(raw)
    if not isinstance(binding, dict) or set(binding) != {'request_media', 'security_bindings', 'header_parameters_input'}:
        raise SourceMappingError('explicit_task_binding_required')
    if binding['header_parameters_input'] != 'task_data.input.headers':
        raise SourceMappingError('unsupported_header_parameter_binding')
    media = binding['request_media']
    if media != 'application/json':
        raise SourceMappingError('only_explicit_json_request_binding_supported')
    bindings = binding['security_bindings']
    if not isinstance(bindings, dict):
        raise SourceMappingError('invalid_security_bindings')
    ops = [(path, method, item, op) for path, item in document.get('paths', {}).items()
           if isinstance(item, dict) for method, op in item.items() if method in METHODS and isinstance(op, dict)]
    explicit = candidate.get('source_operation')
    if explicit is not None:
        if not isinstance(explicit, dict) or set(explicit) != {'path', 'method'}:
            raise SourceMappingError('invalid_explicit_operation')
        ops = [x for x in ops if x[0] == explicit['path'] and x[1] == str(explicit['method']).lower()]
    if len(ops) != 1:
        raise SourceMappingError('one_unambiguous_operation_required')
    path, method, path_item, operation = ops[0]
    origin = '/paths/' + _pointer(path) + '/' + method
    requirements = operation.get('security', document.get('security', []))
    if not isinstance(requirements, list):
        raise SourceMappingError('invalid_security_requirements')
    security_origin = origin + '/security' if 'security' in operation else '/security'
    definitions = document.get('components', {}).get('securitySchemes', {})
    schemes = {}; selected_bindings = {}
    for requirement in requirements:
        if not isinstance(requirement, dict):
            raise SourceMappingError('invalid_security_requirement')
        for name, scopes in requirement.items():
            if not isinstance(scopes, list) or any(not isinstance(x, str) for x in scopes):
                raise SourceMappingError('invalid_security_scopes')
            scheme, _ = _deref(definitions.get(name), document)
            value = bindings.get(name)
            if scheme.get('type') == 'oauth2':
                expected = {'kind': 'bearer', 'token_input': 'task_data.input.auth.access_token',
                            'scopes_input': 'task_data.input.auth.scopes'}
                if value != expected:
                    raise SourceMappingError('explicit_oauth_transport_binding_required')
                if not isinstance(scheme.get('flows'), dict) or not scheme['flows']:
                    raise SourceMappingError('invalid_oauth_flows')
            elif scheme.get('type') == 'apiKey':
                if scheme.get('in') != 'header' or not isinstance(scheme.get('name'), str) or not scheme['name'] or scopes:
                    raise SourceMappingError('unsupported_api_key_scheme')
                if value != {'kind': 'api_key', 'value_input': 'task_data.input.auth.api_key'}:
                    raise SourceMappingError('explicit_api_key_binding_required')
            else:
                raise SourceMappingError('unsupported_security_scheme')
            schemes[name] = {'source_pointer': '/components/securitySchemes/' + _pointer(name),
                             'definition': deepcopy(scheme)}
            selected_bindings[name] = deepcopy(value)
    body = operation.get('requestBody'); body_info = None
    if body is not None:
        body, _ = _deref(body, document)
        if not isinstance(body.get('content'), dict) or media not in body['content']:
            raise SourceMappingError('selected_request_media_not_declared')
        body_info = {'source_pointer': origin + '/requestBody', 'required': body.get('required', False),
                     'declared_media': sorted(body['content']), 'selected_media': media}
    parameters = {}
    for node, pointer in [(path_item, '/paths/' + _pointer(path)), (operation, origin)]:
        if not isinstance(node.get('parameters', []), list):
            raise SourceMappingError('invalid_parameters')
        for i, param in enumerate(node.get('parameters', [])):
            param, _ = _deref(param, document)
            if param.get('in') == 'header':
                name = param.get('name')
                if not isinstance(name, str) or not name or 'content' in param:
                    raise SourceMappingError('unsupported_header_parameter')
                parameters[name.lower()] = {'source_pointer': pointer + '/parameters/' + str(i),
                                            'definition': deepcopy(param)}
    contract = {'operation': {'path': path, 'method': method.upper()},
        'source_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(),
        'source_facts': {'security_pointer': security_origin, 'security_requirements': deepcopy(requirements),
                         'security_schemes': schemes, 'request_body': body_info,
                         'header_parameters': list(parameters.values())},
        'explicit_task_binding': {'security_bindings': selected_bindings,
            'header_parameters_input': binding['header_parameters_input'],
            'selected_request_media': media if body_info else None}}
    if len(json.dumps(contract, ensure_ascii=False, allow_nan=False)) > 24000:
        raise SourceMappingError('request_transport_size_limit')
    return contract


def attach_request_transport(candidate: dict, raw: str, binding: dict) -> list[dict]:
    def skip(reason): return [{'kind': 'request_transport_skipped', 'reason': reason}]
    if candidate.get('prompt_format') != 'literal':
        return skip('explicit_literal_prompt_required')
    prompt = candidate.get('prompt_template')
    if not isinstance(prompt, str) or not prompt.strip():
        return skip('prompt_missing')
    try:
        contract = transport_contract(candidate, raw, binding)
    except Exception as exc:
        return skip(str(exc) if isinstance(exc, SourceMappingError) else 'source_or_binding_parse_error')
    block = START + chr(10) + json.dumps(contract, ensure_ascii=False, sort_keys=True, allow_nan=False) + chr(10) + END
    if START in prompt or END in prompt:
        return [] if block in prompt else skip('existing_request_transport_conflict')
    instructions = (
        'For request construction only, preserve the following source transport requirements and explicit task bindings. '
        'Different security requirement objects are alternatives; all schemes and scopes inside one chosen object are required. '
        'A declared optional header parameter is not a replacement for the chosen security requirement. '
        'Validate credentials and body using the existing task contract before constructing a request; do not turn an invalid input into a valid one. '
        'For a task-bound bearer scheme, copy the supplied access token into Authorization as "Bearer " followed by that token. '
        'For a task-bound header apiKey scheme, copy its supplied value to the exact header name declared by that scheme. '
        'When sending a body in the explicitly selected media type, include Content-Type with that media type; do not add a body or Content-Type to a bodyless operation. '
        'Preserve applicable supplied header parameters separately from required authentication headers. '
        'Do not invent credentials, infer them from examples, or confuse request media with response media. '
        'Keep all existing request validation, response behavior, output structure, URL rules, and supplied values unchanged. '
        'Source facts and the separately declared task transport binding below have distinct provenance.'
    )
    candidate['prompt_template'] = prompt.rstrip() + chr(10) + instructions + chr(10) + block
    return [{'kind': 'request_transport_attached', 'source_sha256': contract['source_sha256'],
             'operation': contract['operation'], 'binding_sha256': hashlib.sha256(json.dumps(
                 contract['explicit_task_binding'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()}]
