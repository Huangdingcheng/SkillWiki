"""Identity-bound verification for new versions and recovery drafts.

This is a compatibility gate, not an evidence generator. Callers may record a
proof only after their existing verifier or evidence gate has actually passed.
"""
from __future__ import annotations

from typing import Any, Iterable
from ...models.skill_model import Skill, SkillEvaluation, SkillMetrics, SkillProvenance, SkillState
from .artifact_identity import canonical_skill_artifact_hash

PROOF_SCHEMA = "skillwiki-current-identity-proof-v1"
REQUIRED_KEY = "fresh_verification_required"


def requires_fresh_verification(skill: Skill) -> bool:
    context = skill.provenance.creation_context if skill.provenance else {}
    return bool(context.get(REQUIRED_KEY) or context.get("recovery_draft")
                or (context.get("source_version") and skill.provenance.parent_skill_ids))


def reset_version_evidence(skill: Skill, *, source_skill: Skill | None = None, reason: str = "new_version") -> Skill:
    """Invalidate results after assigning the new identity and applying overrides."""
    evaluation = (SkillEvaluation.model_validate(skill.evaluation)
                  if isinstance(skill.evaluation, dict) else skill.evaluation.model_copy(deep=True))
    evaluation.harness_validation = {}
    evaluation.validation_summary = None
    skill.evaluation = evaluation
    skill.metrics = SkillMetrics()
    skill.state = SkillState.DRAFT
    skill.released_at = None
    skill.deprecated_at = None
    provenance = (SkillProvenance.model_validate(skill.provenance) if isinstance(skill.provenance, dict)
                  else skill.provenance.model_copy(deep=True) if skill.provenance
                  else SkillProvenance(source_type="adapt"))
    if source_skill is not None:
        provenance.parent_skill_ids = list(dict.fromkeys([*provenance.parent_skill_ids, source_skill.skill_id]))
        provenance.creation_context.setdefault("source_version", source_skill.version)
    provenance.creation_context[REQUIRED_KEY] = True
    provenance.creation_context["verification_reset_reason"] = reason
    skill.provenance = provenance
    return skill


def record_current_identity_proof(skill: Skill, *, source: str, evidence_id: str, trace_ids: Iterable[str] = ()) -> dict[str, Any]:
    """Bind an already successful verification record to the current artifact."""
    if not source.strip() or not evidence_id.strip():
        raise ValueError("Current identity proof requires a source and evidence id")
    proof = {"schema_version": PROOF_SCHEMA, "skill_id": skill.skill_id,
             "skill_version": skill.version, "artifact_hash": canonical_skill_artifact_hash(skill),
             "source": source, "evidence_id": evidence_id, "trace_ids": list(trace_ids), "status": "passed"}
    evaluation = skill.evaluation.model_copy(deep=True)
    evaluation.harness_validation = {**evaluation.harness_validation, "current_identity_proof": proof}
    skill.evaluation = evaluation
    return proof


def require_current_identity_proof(skill: Skill, target_state: SkillState, *, force: bool = False) -> None:
    """Reject derived-identity promotion without matching successful proof.

    Ordinary legacy release keeps its compatibility behavior. The explicit
    review-and-release route forces this check because a review is not a test.
    Evidence-bound release records retain their existing compact shape.
    """
    if target_state not in {SkillState.VERIFIED, SkillState.RELEASED}:
        return
    if not force and not requires_fresh_verification(skill):
        return
    validation = skill.evaluation.harness_validation
    expected_hash = canonical_skill_artifact_hash(skill)
    proof = validation.get("current_identity_proof")
    if isinstance(proof, dict) and (
        proof.get("schema_version") == PROOF_SCHEMA
        and proof.get("skill_id") == skill.skill_id
        and proof.get("skill_version") == skill.version
        and proof.get("artifact_hash") == expected_hash
        and proof.get("status") == "passed"
        and proof.get("source") and proof.get("evidence_id")
    ):
        return
    decision = validation.get("evidence_decision")
    if isinstance(decision, dict) and (
        decision.get("skill_id") == skill.skill_id and decision.get("version") == skill.version
        and decision.get("artifact_hash") == expected_hash and decision.get("decision") == "accept"
        and decision.get("evaluation_id") and decision.get("trace_ids")
        and isinstance(decision.get("applicability"), dict)
        and all(decision["applicability"].get(key) for key in
                ("contract_version", "environment_fingerprint", "verifier_version"))
    ):
        return
    raise ValueError("Fresh verification with current identity proof is required before promotion or release")
