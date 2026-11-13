"""Canonical skill text construction."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from skillwiki._vendor.skillfabric.graph.contracts.models import SkillContract
    from skillwiki._vendor.skillfabric.registry.models import SkillNode


def canonical_skill_text(skill: SkillNode) -> str:
    """Build retrieval text from name, description, and full SKILL.md."""

    return "\n".join(
        [
            skill.name.strip(),
            skill.description.strip(),
            skill.raw_text.strip(),
        ]
    ).strip()


def contract_skill_text(
    skill: SkillNode,
    contract: SkillContract,
    *,
    source_char_limit: int = 2_000,
) -> str:
    """Build the canonical retrieval document for a validated contract."""

    sections = _contract_sections(skill, contract)
    source = _truncate_source(skill.raw_text, source_char_limit)
    if source:
        sections.append(f"Source:\n{source}")
    return "\n".join(sections).strip()


def compact_contract_text(skill: SkillNode, contract: SkillContract) -> str:
    """Build a complete contract document without repeating the full source."""

    return "\n".join(_contract_sections(skill, contract)).strip()


def _contract_sections(skill: SkillNode, contract: SkillContract) -> list[str]:
    return [
        f"Name: {skill.name}",
        f"Description: {skill.description}",
        f"Capability: {contract.capability}",
        f"When to use: {contract.when_to_use}",
        f"Requires: {_contract_fields_text(contract.requires)}",
        f"Produces: {_contract_fields_text(contract.produces)}",
        f"Tools: {_contract_fields_text(contract.tools)}",
    ]


def hash_text(text: str) -> str:
    """Compute a stable text hash."""

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _contract_fields_text(fields) -> str:
    return "; ".join(
        f"{field.name}: {field.description}" if field.description else field.name
        for field in fields
    )


def _truncate_source(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text.strip()
    head = limit * 2 // 3
    tail = limit - head
    return f"{text[:head].rstrip()}\n...\n{text[-tail:].lstrip()}"
