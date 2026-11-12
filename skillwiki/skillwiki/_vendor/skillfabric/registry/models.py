"""Skill registry data models."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

_REQUIRED_KEYS = {"id", "type", "name", "description", "content_hash"}
_OPTIONAL_KEYS = {"raw_text"}


@dataclass(slots=True)
class SkillNode:
    """Skill node in the canonical KG."""

    id: str
    type: Literal["skill"]
    name: str
    description: str
    content_hash: str
    raw_text: str = ""

    def to_dict(self, *, include_raw_text: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "description": self.description,
            "content_hash": self.content_hash,
        }
        if include_raw_text:
            payload["raw_text"] = self.raw_text
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SkillNode:
        missing = _REQUIRED_KEYS - set(payload)
        unexpected = set(payload) - _REQUIRED_KEYS - _OPTIONAL_KEYS
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"missing: {', '.join(sorted(missing))}")
            if unexpected:
                details.append(f"unexpected: {', '.join(sorted(unexpected))}")
            raise ValueError(f"invalid skill node fields ({'; '.join(details)})")
        if payload["type"] != "skill":
            raise ValueError("skill node type must be skill")
        raw_text = payload.get("raw_text", "")
        if not isinstance(raw_text, str):
            raise ValueError("skill node raw_text must be a string")
        return cls(
            id=_required_string(payload["id"], label="skill node id"),
            type="skill",
            name=_required_string(payload["name"], label="skill node name"),
            description=_string(payload["description"], label="skill node description"),
            content_hash=_required_string(
                payload["content_hash"],
                label="skill node content_hash",
            ),
            raw_text=raw_text,
        )


def _string(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _required_string(value: Any, *, label: str) -> str:
    result = _string(value, label=label).strip()
    if not result:
        raise ValueError(f"{label} must be non-empty")
    return result
