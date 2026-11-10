"""Preserve execution contracts across bounded implementation rebuilds."""
from copy import deepcopy
from ...models.skill_model import SkillImplementation


def validate_implementation_binding(implementation):
    """Use the runtime's existing source check without executing a model."""
    if implementation.request_body_validation is not None:
        from types import SimpleNamespace
        from ..skill_runtime.request_body_validation import prepare_request_body_validation
        prepare_request_body_validation(
            SimpleNamespace(implementation=implementation),
            {'task_data': {'kind': 'response', 'input': {}}},
        )
    return implementation


def rebuild_implementation(data, parents):
    """No implicit source rebinding when merge/split changes instructions."""
    values = deepcopy(data)
    implementations = [p.implementation for p in parents if p.implementation]
    modes = {p.prompt_format for p in implementations}
    if values.get('prompt_format') is None and values.get('prompt_template'):
        if len(modes) > 1:
            raise ValueError('ambiguous_prompt_format: explicit mode required')
        values['prompt_format'] = next(iter(modes), None)
    policies = [p.request_body_validation for p in implementations]
    guarded = any(p is not None for p in policies)
    if guarded and values.get('request_body_validation') is None:
        if any(p != policies[0] for p in policies) or policies[0] is None:
            raise ValueError('source_policy_rebinding_required: parents differ')
        values['request_body_validation'] = deepcopy(policies[0])
    implementation = SkillImplementation(**values)
    if guarded or implementation.request_body_validation is not None:
        from ..input_knowledge.api_source_rules import START, END
        prompt = implementation.prompt_template or ''
        if START not in prompt or END not in prompt:
            raise ValueError('source_policy_rebinding_required: structured source contract missing')
    return validate_implementation_binding(implementation)
