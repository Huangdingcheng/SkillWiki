"""Deterministic identity for the semantic Skill artifact projection."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict

from ...models.skill_model import Skill


def artifact_identity_projection(skill: Skill) -> Dict[str, Any]:
    """Return the M1/C03 semantic artifact fields used for identity."""
    implementation = skill.implementation.model_dump(mode="json") if skill.implementation else None
    if implementation is not None and implementation.get("prompt_format") is None:
        # Only this newly introduced optional field is excluded. Existing null
        # fields are part of the historical canonical artifact identity.
        implementation.pop("prompt_format", None)
    if implementation is not None and implementation.get("request_body_validation") is None:
        implementation.pop("request_body_validation", None)
    return {
        "skill_id": skill.skill_id,
        "version": skill.version,
        "name": skill.name,
        "description": skill.description,
        "interface": skill.interface.model_dump(mode="json"),
        "implementation": implementation,
        # Keep the executable evaluation contract in the identity. Runtime
        # validation summaries and harness evidence are mutable governance
        # metadata and must not create a new artifact identity.
        "evaluation": {
            "verifier_specs": skill.evaluation.verifier_specs,
            "test_case_refs": skill.evaluation.test_case_refs,
            "benchmark_task_ids": skill.evaluation.benchmark_task_ids,
        },
    }


def canonical_skill_artifact_json(skill: Skill) -> str:
    """Serialize the semantic artifact projection with canonical LF JSON."""
    return json.dumps(
        artifact_identity_projection(skill),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"


def canonical_skill_artifact_hash(skill: Skill) -> str:
    """Return the SHA-256 identity of the canonical semantic artifact JSON."""
    return hashlib.sha256(canonical_skill_artifact_json(skill).encode("utf-8")).hexdigest()
