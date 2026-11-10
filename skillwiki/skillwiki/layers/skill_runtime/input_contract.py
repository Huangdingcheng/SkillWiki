"""Shared, offline input validation for native execution and research runners."""
from __future__ import annotations

from typing import Any

from ...models.skill_model import Skill


class InputContractError(ValueError):
    """The declared schema or actual input is invalid; dispatch must not run."""


def validate_input_schema(schema: Any, input_data: Any) -> None:
    """Check the actual input without coercion, inference, mutation or network.

    An empty object schema is unconstrained. Unsupported schema dialects and
    unresolved external references fail closed instead of fetching resources.
    """
    try:
        from jsonschema import Draft202012Validator, validators
        from referencing import Registry
        from referencing.exceptions import NoSuchResource

        def deny_remote(uri: str) -> Any:
            raise NoSuchResource(ref=uri)

        if not isinstance(schema, (dict, bool)):
            raise ValueError("input_schema must be a JSON Schema object or boolean")
        validator_type = Draft202012Validator
        if isinstance(schema, dict) and "$schema" in schema:
            validator_type = validators.validator_for(schema, default=None)
            if validator_type is None:
                raise ValueError("input_schema declares an unsupported JSON Schema dialect")
        validator_type.check_schema(schema)
        validator = validator_type(schema, registry=Registry(retrieve=deny_remote))
        errors = list(validator.iter_errors(input_data))
    except Exception as exc:
        raise InputContractError(
            f"Invalid input_schema or unavailable schema reference: {type(exc).__name__}: {exc}"
        ) from exc
    if errors:
        details = "; ".join(
            f"{list(error.absolute_path)}: {error.message}" for error in errors
        )
        raise InputContractError(f"Input data does not satisfy input_schema: {details}")


def validate_skill_input(skill: Skill, input_data: Any) -> None:
    """Validate precisely the data that will be sent to this Skill."""
    try:
        validate_input_schema(skill.interface.input_schema, input_data)
    except InputContractError as exc:
        raise InputContractError(f"Skill {skill.name}: {exc}") from exc
