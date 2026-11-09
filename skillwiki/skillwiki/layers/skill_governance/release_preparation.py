"""Prepare an evidence-bound release without changing the stored object."""
from ...models.skill_model import Skill, SkillEvaluation, SkillState
from .version_evidence import require_current_identity_proof


def prepare_evidence_release(
    skill: Skill, evaluation: SkillEvaluation,
) -> tuple[Skill, list[dict[str, str]]]:
    """Validate the existing evidence-release path without mutating storage.

    The caller has accepted the evidence and audited the candidate. Reuse the
    model state machine and identity gate for every step, including the draft
    verification step. A failure must leave even an aliased memory object intact.
    """
    candidate = skill.model_copy(deep=True)
    candidate.evaluation = evaluation.model_copy(deep=True)
    targets = ([SkillState.VERIFIED] if candidate.state == SkillState.DRAFT else [])
    targets.append(SkillState.RELEASED)
    transitions = []
    for target in targets:
        previous = candidate.state
        # This branch explicitly publishes evidence, including legacy objects.
        # Do not use the ordinary legacy-release exemption for supplied proof.
        require_current_identity_proof(candidate, target, force=True)
        candidate.transition_to(target)
        transitions.append({"from_state": previous.value, "to_state": target.value})
    return candidate, transitions
