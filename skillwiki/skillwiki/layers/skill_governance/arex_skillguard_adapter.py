"""Narrow governance adapters for AREX-Skill and the SkillGuard paper.

The two upstream references deliberately remain governance signals. AREX's
refresh workflow can be observed through a pinned upstream subprocess and Git
delta helper; the caller owns generation and review. The SkillGuard paper has no
verified executable package in this repository, so its adapter only classifies
contract drift for review.  Neither adapter can publish, replace, or execute a
skill.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Iterable, Literal, Mapping

from .upstream_adapters import source_refresh_event

AREX_COMPONENT = "arex-skill-refresh"
AREX_REPOSITORY = "VectorSpaceLab/AREX-Skill"
AREX_PINNED_COMMIT = "ac3fe1afa80fb9a09775ecfb2b6cc3ba850a2db6"
AREX_LICENSE = "Apache-2.0"

# This is the paper identity, not the same-name AGPL malware scanner project.
SKILLGUARD_SOURCE = "arXiv:2605.10990v1"
SKILLGUARD_TITLE = (
    "Skill Drift Is Contract Violation: Proactive Maintenance for LLM Agent Skill Libraries"
)

AREX_REFRESH_STATES = frozenset({"unchanged", "updated", "stale", "removed", "unknown"})
CONTRACT_CHANGE_KINDS = frozenset(
    {
        "comment_only",
        "required_dependency_drift",
        "required_api_drift",
        "unknown_environment",
    }
)
CONTRACT_STATUSES = frozenset({"pass", "block", "defer"})
ENVIRONMENT_STATUSES = frozenset({"known", "unknown", "unavailable"})


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field} must be a non-empty single-line string")
    return value.strip()


def _evidence(values: Iterable[Any]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError("evidence must be a sequence")
    return tuple(_text(value, "evidence[]") for value in values)


@dataclass(frozen=True)
class ArexRefreshAssessment:
    """Auditable source-refresh state; all states require human review."""

    source_commit: str
    observed_state: str
    evidence: tuple[str, ...] = ()
    component: str = AREX_COMPONENT
    repository: str = AREX_REPOSITORY
    pinned_commit: str = AREX_PINNED_COMMIT
    license: str = AREX_LICENSE
    event: str = "source_refresh_observed"
    action: str = "defer_until_review"
    review_required: bool = True
    promotion_allowed: bool = False
    auto_promote: bool = False
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _text(self.source_commit, "source_commit"))
        object.__setattr__(self, "observed_state", _text(self.observed_state, "observed_state"))
        if self.observed_state not in AREX_REFRESH_STATES:
            raise ValueError(f"unsupported AREX refresh state: {self.observed_state}")
        object.__setattr__(self, "evidence", _evidence(self.evidence))
        if self.component != AREX_COMPONENT or self.repository != AREX_REPOSITORY:
            raise ValueError(
                "AREX assessment identity is fixed to the verified upstream repository"
            )
        if self.pinned_commit != AREX_PINNED_COMMIT or self.license != AREX_LICENSE:
            raise ValueError(
                "AREX pinned commit/license identity does not match the verified source"
            )
        if self.promotion_allowed or self.auto_promote:
            raise ValueError("AREX refresh assessment cannot auto-promote")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence"] = list(self.evidence)
        result["source_identity"] = {
            "repository": self.repository,
            "pinned_commit": self.pinned_commit,
            "license": self.license,
        }
        return result


def assess_arex_refresh(
    *,
    source_commit: str,
    observed_state: str,
    evidence: Iterable[str] = (),
) -> ArexRefreshAssessment:
    """Classify a source change and preserve the existing event contract.

    ``source_refresh_event`` remains the canonical state vocabulary.  This
    wrapper adds pinned provenance and an explicit no-promotion guarantee;
    callers can pass the result to ``propose_skill_change`` only after review.
    """

    normalized_state = _text(observed_state, "observed_state")
    if normalized_state not in AREX_REFRESH_STATES:
        raise ValueError(f"unsupported AREX refresh state: {normalized_state}")
    event = source_refresh_event(
        AREX_COMPONENT,
        source_commit=source_commit,
        observed_state=normalized_state,
        evidence=list(evidence),
    )
    state = event["observed_state"]
    reason = {
        "unchanged": "source is unchanged; retain current release pending review",
        "updated": "source changed; prepare a candidate diff for review",
        "stale": "existing skill guidance may be stale; defer release",
        "removed": "source capability was removed; defer release",
        "unknown": "source state could not be established; defer release",
    }[state]
    return ArexRefreshAssessment(
        source_commit=event["source_commit"],
        observed_state=state,
        evidence=tuple(event["evidence"]),
        action="defer_until_review",
        reason=reason,
    )


@dataclass(frozen=True)
class SkillGuardAssessment:
    """Conceptual SkillGuard paper-direction contract-drift decision."""

    change_kind: str
    status: str
    reason: str
    environment_status: str = "known"
    evidence: tuple[str, ...] = ()
    source: str = SKILLGUARD_SOURCE
    title: str = SKILLGUARD_TITLE
    executable_package: str | None = None
    executable_verified: bool = False
    promotion_allowed: bool = False
    review_required: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "change_kind", _text(self.change_kind, "change_kind"))
        object.__setattr__(self, "status", _text(self.status, "status"))
        object.__setattr__(self, "reason", _text(self.reason, "reason"))
        object.__setattr__(
            self, "environment_status", _text(self.environment_status, "environment_status")
        )
        object.__setattr__(self, "evidence", _evidence(self.evidence))
        if self.status not in CONTRACT_STATUSES:
            raise ValueError(f"unsupported SkillGuard status: {self.status}")
        if self.environment_status not in ENVIRONMENT_STATUSES:
            raise ValueError(f"unsupported environment status: {self.environment_status}")
        if self.source != SKILLGUARD_SOURCE:
            raise ValueError("SkillGuard identity is fixed to arXiv:2605.10990v1")
        if self.executable_package is not None or self.executable_verified:
            raise ValueError("the SkillGuard paper direction has no verified executable package")
        if self.promotion_allowed:
            raise ValueError("conceptual SkillGuard assessment cannot promote a skill")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["evidence"] = list(self.evidence)
        result["source_identity"] = {
            "source": self.source,
            "title": self.title,
            "executable_package": None,
            "executable_verified": False,
        }
        result["conceptual_only"] = True
        return result


def assess_skillguard_contract_drift(
    *,
    change_kind: str,
    environment_status: str = "known",
    evidence: Iterable[str] = (),
) -> SkillGuardAssessment:
    """Classify contract drift without pretending to run the paper's method.

    A comment-only change does not alter an executable contract and passes.
    A known required dependency/API drift blocks use until a reviewed update
    exists.  Unknown or unavailable environment facts defer the decision.
    Unrecognised kinds and environment values also fail closed as ``defer``.
    """

    kind = _text(change_kind, "change_kind")
    env = _text(environment_status, "environment_status")
    refs = _evidence(evidence)
    if env not in ENVIRONMENT_STATUSES:
        status, reason = (
            "defer",
            "environment status is unrecognised; contract applicability is unknown",
        )
    elif kind == "comment_only" and env == "known":
        status, reason = "pass", "comment-only change does not alter an executable contract"
    elif kind == "unknown_environment" or env in {"unknown", "unavailable"}:
        status, reason = "defer", "environment contract cannot be established"
    elif kind in {"required_dependency_drift", "required_api_drift"}:
        status, reason = (
            "block",
            "required dependency/API contract drift must be reviewed before execution",
        )
    else:
        status, reason = "defer", "change kind is unrecognised; fail closed pending contract review"
    return SkillGuardAssessment(
        change_kind=kind,
        status=status,
        reason=reason,
        environment_status=env,
        evidence=refs,
    )


def observe_arex_source_refresh(**kwargs: Any) -> dict[str, Any]:
    """Observe real Git/source provenance; retain the legacy classifier above."""
    from .component_checks import observe_arex_source_refresh as observe
    return observe(**kwargs)


def build_arex_refresh_prompt(**kwargs: Any) -> str:
    """Build a source-grounded prompt without making a model request."""
    from .component_checks import build_arex_refresh_prompt as build
    return build(**kwargs)


def inspect_skillguard_environment(**kwargs: Any) -> dict[str, Any]:
    """Observe the local environment using the paper-inspired host contract."""
    from .component_checks import inspect_skillguard_environment as inspect
    return inspect(**kwargs)


__all__ = [
    "AREX_COMPONENT",
    "AREX_REPOSITORY",
    "AREX_PINNED_COMMIT",
    "AREX_LICENSE",
    "SKILLGUARD_SOURCE",
    "SKILLGUARD_TITLE",
    "AREX_REFRESH_STATES",
    "CONTRACT_CHANGE_KINDS",
    "CONTRACT_STATUSES",
    "ArexRefreshAssessment",
    "SkillGuardAssessment",
    "assess_arex_refresh",
    "assess_skillguard_contract_drift",
    "observe_arex_source_refresh",
    "build_arex_refresh_prompt",
    "inspect_skillguard_environment",
]

