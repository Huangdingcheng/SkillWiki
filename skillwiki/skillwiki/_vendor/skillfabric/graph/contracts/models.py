"""Canonical SkillContract schema and source-evidence validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from skillwiki._vendor.skillfabric.graph.models import EvidenceRef
from skillwiki._vendor.skillfabric.registry.models import SkillNode

_CONTRACT_KEYS = frozenset(
    {
        "capability",
        "when_to_use",
        "requires",
        "produces",
        "tools",
        "evidence",
    }
)
_FIELD_KEYS = frozenset({"name", "description", "evidence"})
_EVIDENCE_KEYS = frozenset({"line"})
_SERIALIZED_KEYS = frozenset(
    {
        "skill_id",
        "content_hash",
        "capability",
        "when_to_use",
        "requires",
        "produces",
        "tools",
        "evidence",
    }
)
_EMPTY_SOURCE_SERIALIZED_KEYS = _SERIALIZED_KEYS | {"source_status"}
_EMPTY_SOURCE_CAPABILITY = (
    "No evidence-grounded capability is available because the skill source is empty."
)
_EMPTY_SOURCE_WHEN_TO_USE = (
    "Do not select from contract evidence because no skill source is available."
)


class ContractSchemaError(ValueError):
    """Raised when an extracted contract is not source-grounded or schema-valid."""


@dataclass(frozen=True, slots=True)
class ContractField:
    """One concrete prerequisite, outcome, or tool in a skill contract."""

    name: str
    description: str
    evidence: tuple[EvidenceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "evidence": [item.to_dict() for item in self.evidence],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ContractField:
        _require_exact_keys(payload, _FIELD_KEYS, label="contract field")
        return cls(
            name=_required_string(payload, "name", label="contract field"),
            description=_required_string(payload, "description", label="contract field"),
            evidence=tuple(
                _require_evidence(
                    _evidence_from_serialized(payload.get("evidence")),
                    label="contract field evidence",
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class SkillContract:
    """Evidence-grounded operational contract extracted once for a skill."""

    skill_id: str
    content_hash: str
    capability: str
    when_to_use: str
    requires: tuple[ContractField, ...]
    produces: tuple[ContractField, ...]
    tools: tuple[ContractField, ...]
    evidence: tuple[EvidenceRef, ...]
    source_status: Literal["available", "empty"] = "available"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "skill_id": self.skill_id,
            "content_hash": self.content_hash,
            "capability": self.capability,
            "when_to_use": self.when_to_use,
            "requires": [item.to_dict() for item in self.requires],
            "produces": [item.to_dict() for item in self.produces],
            "tools": [item.to_dict() for item in self.tools],
            "evidence": [item.to_dict() for item in self.evidence],
        }
        if self.source_status == "empty":
            payload["source_status"] = "empty"
        return payload

    @classmethod
    def from_empty_source(cls, skill: SkillNode) -> SkillContract:
        """Create a deterministic, non-semantic contract for an empty Skill source."""

        if skill.raw_text.strip():
            raise ContractSchemaError("empty-source contract received a non-empty source")
        return cls(
            skill_id=skill.id,
            content_hash=skill.content_hash,
            capability=_EMPTY_SOURCE_CAPABILITY,
            when_to_use=_EMPTY_SOURCE_WHEN_TO_USE,
            requires=(),
            produces=(),
            tools=(),
            evidence=(),
            source_status="empty",
        )

    @classmethod
    def from_extraction(
        cls,
        skill: SkillNode,
        payload: dict[str, Any],
    ) -> SkillContract:
        """Validate an LLM payload against the exact schema and original source."""

        _require_exact_keys(payload, _CONTRACT_KEYS, label="contract")
        source_lines = skill.raw_text.splitlines()
        return cls(
            skill_id=skill.id,
            content_hash=skill.content_hash,
            capability=_required_string(payload, "capability", label="contract"),
            when_to_use=_required_string(payload, "when_to_use", label="contract"),
            requires=tuple(
                _fields_from_extraction(
                    payload.get("requires"),
                    field_name="requires",
                    skill=skill,
                    source_lines=source_lines,
                )
            ),
            produces=tuple(
                _fields_from_extraction(
                    payload.get("produces"),
                    field_name="produces",
                    skill=skill,
                    source_lines=source_lines,
                )
            ),
            tools=tuple(
                _fields_from_extraction(
                    payload.get("tools"),
                    field_name="tools",
                    skill=skill,
                    source_lines=source_lines,
                )
            ),
            evidence=tuple(
                _require_evidence(
                    _evidence_from_extraction(
                        payload.get("evidence"),
                        skill=skill,
                        source_lines=source_lines,
                        label="contract evidence",
                    ),
                    label="contract evidence",
                )
            ),
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SkillContract:
        """Load a previously validated canonical contract."""

        source_status = payload.get("source_status", "available")
        expected_keys = (
            _EMPTY_SOURCE_SERIALIZED_KEYS if source_status == "empty" else _SERIALIZED_KEYS
        )
        _require_exact_keys(payload, expected_keys, label="serialized contract")
        requires = tuple(_fields_from_serialized(payload.get("requires"), "requires"))
        produces = tuple(_fields_from_serialized(payload.get("produces"), "produces"))
        tools = tuple(_fields_from_serialized(payload.get("tools"), "tools"))
        evidence = tuple(_evidence_from_serialized(payload.get("evidence")))
        capability = _required_string(payload, "capability", label="serialized contract")
        when_to_use = _required_string(payload, "when_to_use", label="serialized contract")
        if source_status == "empty":
            if requires or produces or tools or evidence:
                raise ContractSchemaError("empty-source contract must not contain semantic fields")
            if capability != _EMPTY_SOURCE_CAPABILITY or when_to_use != _EMPTY_SOURCE_WHEN_TO_USE:
                raise ContractSchemaError("empty-source contract must use deterministic sentinels")
        else:
            evidence = tuple(_require_evidence(list(evidence), label="contract evidence"))
        return cls(
            skill_id=_required_string(payload, "skill_id", label="serialized contract"),
            content_hash=_required_string(payload, "content_hash", label="serialized contract"),
            capability=capability,
            when_to_use=when_to_use,
            requires=requires,
            produces=produces,
            tools=tools,
            evidence=evidence,
            source_status=source_status,
        )


def _fields_from_extraction(
    value: Any,
    *,
    field_name: str,
    skill: SkillNode,
    source_lines: list[str],
) -> list[ContractField]:
    if not isinstance(value, list):
        raise ContractSchemaError(f"{field_name} must be a list")
    fields: list[ContractField] = []
    seen_names: set[str] = set()
    for index, item in enumerate(value):
        label = f"{field_name}[{index}]"
        if not isinstance(item, dict):
            raise ContractSchemaError(f"{label} must be an object")
        _require_exact_keys(item, _FIELD_KEYS, label=label)
        name = _required_string(item, "name", label=label)
        normalized_name = " ".join(name.lower().replace("_", " ").replace("-", " ").split())
        if normalized_name in seen_names:
            continue
        seen_names.add(normalized_name)
        evidence = _require_evidence(
            _evidence_from_extraction(
                item.get("evidence"),
                skill=skill,
                source_lines=source_lines,
                label=f"{label}.evidence",
            ),
            label=f"{label}.evidence",
        )
        fields.append(
            ContractField(
                name=name,
                description=_required_string(item, "description", label=label),
                evidence=tuple(evidence),
            )
        )
    return fields


def _evidence_from_extraction(
    value: Any,
    *,
    skill: SkillNode,
    source_lines: list[str],
    label: str,
) -> list[EvidenceRef]:
    if not isinstance(value, list):
        raise ContractSchemaError(f"{label} must be a list")
    evidence: list[EvidenceRef] = []
    out_of_range = 0
    for index, item in enumerate(value):
        item_label = f"{label}[{index}]"
        if not isinstance(item, dict):
            raise ContractSchemaError(f"{item_label} must be an object")
        _require_exact_keys(item, _EVIDENCE_KEYS, label=item_label)
        line = item.get("line")
        if isinstance(line, bool) or not isinstance(line, int):
            raise ContractSchemaError(f"{item_label}.line must be an integer")
        if line < 1 or line > len(source_lines):
            out_of_range += 1
            continue
        source_text = source_lines[line - 1]
        if not source_text.strip():
            continue
        evidence.append(
            EvidenceRef(
                skill=skill.id,
                line=line,
                text=source_text,
            )
        )
    if not evidence and out_of_range:
        raise ContractSchemaError(
            f"{label} contains no valid source lines; source has {len(source_lines)} lines"
        )
    return evidence


def _fields_from_serialized(value: Any, field_name: str) -> list[ContractField]:
    if not isinstance(value, list):
        raise ContractSchemaError(f"{field_name} must be a list")
    fields: list[ContractField] = []
    for item in value:
        if not isinstance(item, dict):
            raise ContractSchemaError(f"{field_name} items must be objects")
        fields.append(ContractField.from_dict(item))
    return fields


def _evidence_from_serialized(value: Any) -> list[EvidenceRef]:
    if not isinstance(value, list):
        raise ContractSchemaError("evidence must be a list")
    evidence: list[EvidenceRef] = []
    for item in value:
        if not isinstance(item, dict):
            raise ContractSchemaError("evidence items must be objects")
        if set(item) != {"skill", "line", "text"}:
            raise ContractSchemaError("serialized evidence has unexpected keys")
        line = item.get("line")
        if isinstance(line, bool) or not isinstance(line, int) or line < 1:
            raise ContractSchemaError("serialized evidence line must be a positive integer")
        evidence.append(
            EvidenceRef(
                skill=_required_string(item, "skill", label="serialized evidence"),
                line=line,
                text=_required_string(item, "text", label="serialized evidence", strip=False),
            )
        )
    return evidence


def _require_evidence(evidence: list[EvidenceRef], *, label: str) -> list[EvidenceRef]:
    if not evidence:
        raise ContractSchemaError(f"{label} must contain at least one source line")
    return evidence


def _require_exact_keys(payload: dict[str, Any], expected: frozenset[str], *, label: str) -> None:
    actual = set(payload)
    missing = expected - actual
    unexpected = actual - expected
    if missing:
        raise ContractSchemaError(f"{label} missing keys: {', '.join(sorted(missing))}")
    if unexpected:
        raise ContractSchemaError(f"{label} has unexpected keys: {', '.join(sorted(unexpected))}")


def _required_string(
    payload: dict[str, Any],
    key: str,
    *,
    label: str,
    strip: bool = True,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise ContractSchemaError(f"{label}.{key} must be a string")
    if not value.strip():
        raise ContractSchemaError(f"{label}.{key} must not be empty")
    return value.strip() if strip else value
