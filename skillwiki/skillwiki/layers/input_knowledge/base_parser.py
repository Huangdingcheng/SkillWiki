"""Shared parser abstractions for the SkillWiki input knowledge layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceUnit
from ...utils.llm_client import LLMClient, Message


@dataclass
class ParseResult:
    """Container returned by input parsers."""

    experience_units: List[ExperienceUnit] = field(default_factory=list)
    raw_input: str = ""
    parse_model: str = ""
    token_usage: int = 0
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return len(self.experience_units) > 0 and not self.errors

    @property
    def unit_count(self) -> int:
        return len(self.experience_units)


class BaseParser(ABC):
    """Base class for parsers that turn raw inputs into ExperienceUnit objects."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    @abstractmethod
    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        """Parse raw input and return extracted experience units."""

    def _build_system_prompt(self) -> str:
        return (
            "You are a SkillWiki knowledge extraction specialist. "
            "Extract reusable operational experience from raw input so it can become Skill candidates. "
            "Return valid JSON only, with no markdown fence or extra explanation."
        )

    async def _call_llm(self, user_prompt: str, system_prompt: Optional[str] = None) -> str:
        messages = [Message.user(user_prompt)]
        if system_prompt:
            messages.insert(0, Message.system(system_prompt))
        response = self._llm.chat(messages)
        return response.content
