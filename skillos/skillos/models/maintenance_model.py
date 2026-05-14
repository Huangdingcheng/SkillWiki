"""Maintenance proposal model for D-side self-management."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MaintenanceProposalStatus(str, Enum):
    """Human-review lifecycle for a maintenance proposal."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class ReflectionMemoryStatus(str, Enum):
    """Lifecycle for a runtime reflection memory item."""

    OBSERVED = "observed"
    PROPOSED = "proposed"
    DISMISSED = "dismissed"


class MaintenanceValidationStatus(str, Enum):
    """Validation lifecycle for a candidate maintenance change."""

    UNTESTED = "untested"
    REPAIRED = "repaired"
    VERIFIED = "verified"
    FAILED = "failed"
    MERGED = "merged"
    DEPRIORITIZED = "deprioritized"


class MaintenanceTrigger(str, Enum):
    """Reason a proposal was created."""

    VERIFIER_FAILED = "verifier_failed"
    LOW_SUCCESS_RATE = "low_success_rate"
    RUNTIME_FAILURE = "runtime_failure"
    AUDIT_FAILED = "audit_failed"
    STALE_SKILL = "stale_skill"
    MANUAL = "manual"


class MaintenanceRecommendedAction(str, Enum):
    """Allowed D-side proposal actions."""

    REPAIR = "repair"
    REVIEW = "review"
    SPLIT = "split"
    MERGE = "merge"
    DEPRECATE = "deprecate"
    NO_ACTION = "no_action"


class ReflectionMemoryEntry(BaseModel):
    """Persisted runtime reflection evidence before a Skill update is proposed."""

    memory_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str = ""
    skill_id: str
    goal: str = ""
    success: bool = False
    failure_signature: str = ""
    reflection_text: str = ""
    evidence: List[str] = Field(default_factory=list)
    verifier_result: Dict[str, Any] = Field(default_factory=dict)
    trajectory_summary: str = ""
    human_decision: str = ""
    status: ReflectionMemoryStatus = ReflectionMemoryStatus.OBSERVED
    source: str = "runtime_reflection"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("skill_id")
    @classmethod
    def validate_skill_id(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("skill_id must not be blank")
        return value

    @field_validator("task_id", "goal", "failure_signature", "reflection_text", "trajectory_summary")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("human_decision")
    @classmethod
    def normalize_human_decision(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, values: List[str]) -> List[str]:
        return [str(value).strip() for value in values if str(value).strip()]

    def mark_proposed(self) -> None:
        """Mark that this memory has contributed to a maintenance proposal."""
        self.status = ReflectionMemoryStatus.PROPOSED
        self.updated_at = _utc_now()


class MaintenanceProposal(BaseModel):
    """Auditable proposal produced before mutating a canonical Skill."""

    proposal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    skill_id: str
    trigger: MaintenanceTrigger
    recommended_action: MaintenanceRecommendedAction = MaintenanceRecommendedAction.REVIEW
    evidence: List[str] = Field(default_factory=list)
    root_cause: str = ""
    patch_hint: str = ""
    feedback_sources: List[str] = Field(default_factory=list)
    targets_to_fix: List[str] = Field(default_factory=list)
    invariants_to_preserve: List[str] = Field(default_factory=list)
    validation_plan: List[str] = Field(default_factory=list)
    validation_status: MaintenanceValidationStatus = MaintenanceValidationStatus.UNTESTED
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1)
    reviewer_notes: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    requires_human_review: bool = True
    status: MaintenanceProposalStatus = MaintenanceProposalStatus.PENDING
    source: str = "skillos_d_self_management"
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("skill_id")
    @classmethod
    def validate_skill_id(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("skill_id must not be blank")
        return value

    @field_validator("evidence")
    @classmethod
    def normalize_evidence(cls, values: List[str]) -> List[str]:
        return [str(value).strip() for value in values if str(value).strip()]

    @field_validator(
        "feedback_sources",
        "targets_to_fix",
        "invariants_to_preserve",
        "validation_plan",
    )
    @classmethod
    def normalize_string_list(cls, values: List[str]) -> List[str]:
        return [str(value).strip() for value in values if str(value).strip()]

    @field_validator("root_cause")
    @classmethod
    def normalize_root_cause(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("reviewer_notes")
    @classmethod
    def normalize_reviewer_notes(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("patch_hint")
    @classmethod
    def normalize_patch_hint(cls, value: str) -> str:
        return str(value or "").strip()

    def accept(self) -> None:
        """Mark the proposal as accepted by a reviewer."""
        self.status = MaintenanceProposalStatus.ACCEPTED
        self.updated_at = _utc_now()

    def reject(self) -> None:
        """Mark the proposal as rejected by a reviewer."""
        self.status = MaintenanceProposalStatus.REJECTED
        self.updated_at = _utc_now()

    def record_attempt(self) -> None:
        """Record one bounded repair/review attempt without changing status."""
        self.attempt_count += 1
        self.updated_at = _utc_now()

    def supersede(self) -> None:
        """Mark the proposal as replaced by a newer proposal."""
        self.status = MaintenanceProposalStatus.SUPERSEDED
        self.updated_at = _utc_now()

    @classmethod
    def from_verifier_failure(
        cls,
        *,
        skill_id: str,
        issues: List[str],
        suggestions: Optional[List[str]] = None,
        confidence: float = 0.8,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "MaintenanceProposal":
        """Create a repair proposal from deterministic verifier failures."""
        evidence = issues or ["Verifier failed without a detailed issue."]
        hint = "; ".join(suggestions or []) or "Inspect verifier failures and repair the Skill."
        return cls(
            skill_id=skill_id,
            trigger=MaintenanceTrigger.VERIFIER_FAILED,
            recommended_action=MaintenanceRecommendedAction.REPAIR,
            evidence=evidence,
            root_cause=evidence[0],
            patch_hint=hint,
            feedback_sources=["deterministic_verifier"],
            targets_to_fix=evidence,
            invariants_to_preserve=[
                "Preserve existing Skill interface and previously passing verifier behavior."
            ],
            validation_plan=[
                "Rerun the deterministic verifier specs that produced this failure.",
                "Confirm the repaired Skill satisfies its postconditions before release.",
            ],
            confidence=confidence,
            requires_human_review=True,
            source="runtime_verifier",
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_health_report(
        cls,
        report: Any,
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional["MaintenanceProposal"]:
        """Create a proposal when monitor health indicates a degraded Skill."""
        status = getattr(getattr(report, "status", None), "value", getattr(report, "status", ""))
        status = str(status)
        if status not in {"degraded", "critical", "stale"}:
            return None

        skill_id = str(getattr(report, "skill_id", "") or "")
        success_rate = float(getattr(report, "success_rate", 0.0) or 0.0)
        issues = list(getattr(report, "issues", []) or [])
        recommendations = list(getattr(report, "recommendations", []) or [])

        if status == "stale":
            action = MaintenanceRecommendedAction.REVIEW
            trigger = MaintenanceTrigger.STALE_SKILL
            confidence = 0.55
        else:
            action = MaintenanceRecommendedAction.REPAIR
            trigger = MaintenanceTrigger.LOW_SUCCESS_RATE
            confidence = max(0.4, min(0.95, 1.0 - success_rate))

        evidence = issues or [f"Skill health status is {status} with success_rate={success_rate:.2f}."]
        hint = "; ".join(str(item) for item in recommendations if str(item).strip())
        if not hint:
            hint = "Review recent failures and propose a targeted repair."

        return cls(
            skill_id=skill_id,
            trigger=trigger,
            recommended_action=action,
            evidence=evidence,
            root_cause=evidence[0],
            patch_hint=hint,
            feedback_sources=["health_monitor"],
            targets_to_fix=evidence,
            invariants_to_preserve=[
                "Preserve public Skill inputs, outputs, and successful recent behavior."
            ],
            validation_plan=[
                "Review recent execution history for repeated failure patterns.",
                "Rerun the affected benchmark or verifier after the proposed change.",
            ],
            confidence=confidence,
            requires_human_review=True,
            source="skill_monitor",
            metadata={"health_status": status, "success_rate": success_rate, **dict(metadata or {})},
        )

    @classmethod
    def from_reflection_proposal(
        cls,
        proposal: Dict[str, Any],
        *,
        task_id: str = "",
        goal: str = "",
    ) -> Optional["MaintenanceProposal"]:
        """Normalize a runtime Reflection proposal into D's canonical model."""
        skill_id = str(proposal.get("skill_id") or "").strip()
        if not skill_id:
            return None

        raw_action = str(proposal.get("recommended_action") or "review").strip()
        action = _safe_action(raw_action)
        evidence = _string_list(proposal.get("evidence"))
        issue = str(proposal.get("issue") or "").strip()
        if issue:
            evidence.insert(0, issue)
        evidence = list(dict.fromkeys(evidence)) or ["Runtime reflection reported a failure."]

        return cls(
            skill_id=skill_id,
            trigger=MaintenanceTrigger.RUNTIME_FAILURE,
            recommended_action=action,
            evidence=evidence,
            root_cause=issue or evidence[0],
            patch_hint=str(proposal.get("proposed_fix") or "Review reflected failure and repair if needed."),
            feedback_sources=["runtime_reflection"],
            targets_to_fix=_string_list(proposal.get("targets_to_fix")) or evidence,
            invariants_to_preserve=_string_list(proposal.get("invariants_to_preserve")) or [
                "Preserve successful trajectories for the same task family when available."
            ],
            validation_plan=_string_list(proposal.get("validation_plan")) or [
                "Replay the failed task after applying the candidate repair.",
                "Check that the same failure evidence no longer appears in the trace.",
            ],
            confidence=0.7 if action == MaintenanceRecommendedAction.REPAIR else 0.5,
            requires_human_review=True,
            source="runtime_reflection",
            metadata={"task_id": task_id, "goal": goal},
        )


def _safe_action(value: str) -> MaintenanceRecommendedAction:
    allowed = {item.value for item in MaintenanceRecommendedAction}
    if value not in allowed:
        value = MaintenanceRecommendedAction.REVIEW.value
    return MaintenanceRecommendedAction(value)


def _string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
