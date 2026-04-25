"""输入知识层基础类 — 定义解析器接口和公共数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceUnit
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ParseResult:
    """解析结果容器。"""
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
    """所有输入解析器的抽象基类。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    @abstractmethod
    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        """解析原始输入，返回 ExperienceUnit 列表。"""

    def _build_system_prompt(self) -> str:
        """子类可覆盖，提供特定的系统提示词。"""
        return (
            "你是 SkillOS 的知识提取专家。"
            "你的任务是从原始输入中提取结构化的操作经验，"
            "识别可复用的操作模式，为后续 Skill 生成做准备。"
            "请严格按照 JSON 格式输出，不要添加额外解释。"
        )

    async def _call_llm(self, user_prompt: str, system_prompt: Optional[str] = None) -> str:
        """调用 LLM，返回响应文本。"""
        from ...utils.llm_client import Message
        messages = [Message.user(user_prompt)]
        if system_prompt:
            messages.insert(0, Message.system(system_prompt))
        response = self._llm.chat(messages)
        return response.content
