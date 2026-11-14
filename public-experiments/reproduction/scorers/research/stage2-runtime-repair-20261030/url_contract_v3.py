"""Repair-only URL representation contract; no execution or publication side effects.

Historical v2 files and results are immutable. Only two exact URL strings are
equivalent: the oracle's absolute URL and its origin-relative path plus query.
This is a post-run evaluator correction, not a new model observation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from urllib.parse import urlsplit

import evidence_tools as v2
from semantic_contract import semantic_specs as v2_specs

RULE_VERSION = 'api-url-representation-v3-20261031'


def url_representations(expected, service_base_url):
    if not isinstance(service_base_url, str):
        raise ValueError('SERVICE_BASE_URL_REQUIRED')
    base = urlsplit(service_base_url)
    if (base.scheme not in ('http', 'https') or not base.netloc or
            base.username is not None or base.password is not None or
            '?' in service_base_url or '#' in service_base_url or
            '\\' in service_base_url or any(c.isspace() for c in service_base_url)):
        raise ValueError('INVALID_SERVICE_BASE_URL')
    request = expected.get('request')
    if request is None:
        return []
    absolute = request['url']
    target = urlsplit(absolute)
    prefix = base.path.rstrip('/')
    if (target.scheme != base.scheme or target.netloc != base.netloc or
            target.username is not None or target.password is not None or
            '#' in absolute or '\\' in absolute or
            any(c.isspace() for c in absolute) or
            not (target.path == prefix or target.path.startswith(prefix + '/'))):
        raise ValueError('ORACLE_URL_OUTSIDE_SERVICE_CONTRACT')
    origin = f'{base.scheme}://{base.netloc}'
    if not absolute.startswith(origin + '/'):
        raise ValueError('ORACLE_URL_NOT_CANONICAL_ABSOLUTE')
    relative = absolute[len(origin):]
    if not relative.startswith('/') or relative.startswith('//'):
        raise ValueError('ORACLE_URL_NOT_ORIGIN_RELATIVE')
    return [absolute, relative]


def semantic_specs(expected, case, *, service_base_url, doc=None, oracle_module=None):
    specs = v2_specs(expected, case, doc, oracle_module)
    alternatives = url_representations(expected, service_base_url)
    if alternatives:
        specs[0]['schema']['properties']['request']['properties']['url'] = {'enum': alternatives}
    return specs


def grade(case, raw, *, service_base_url, expected=None, grader=None,
          execution_status='success', enabled=True):
    target = expected if expected is not None else v2.oracle.expected(case)
    alternatives = url_representations(target, service_base_url)
    delegate = grader or v2.grade
    comparison_raw = raw
    representation = 'not_matched'
    if execution_status == 'success' and enabled and alternatives:
        try:
            parsed = v2.oracle.strict_load(raw)
            request = parsed.get('request')
            if isinstance(request, dict) and request.get('url') in alternatives:
                representation = 'absolute' if request['url'] == alternatives[0] else 'origin_relative'
                comparison = copy.deepcopy(parsed)
                comparison['request']['url'] = alternatives[0]
                comparison_raw = json.dumps(comparison, ensure_ascii=False, allow_nan=False)
        except (ValueError, TypeError):
            pass
    result = delegate(case, comparison_raw, execution_status=execution_status, enabled=enabled)
    return {**result, 'rule_version': RULE_VERSION, 'url_representation': representation,
            'raw_answer_sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest()}
