"""Skill Builder Agent — 从任务/轨迹/文档生成 Skill 草稿。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill, SkillImplementation, SkillInterface, SkillProvenance, SkillType
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SkillDraft:
    skill: Skill
    confidence: float
    source_type: str
    raw_input: str = ""
    build_notes: str = ""


_BUILD_FROM_TASK_PROMPT = """
你是 SkillOS 的 Skill Builder Agent，负责从任务描述中提取并生成 Skill。

## 输入任务
{task_description}

## 上下文
{context}

## 要求
从任务中识别可复用的原子操作，生成 Skill 定义。

## 输出格式（严格 JSON）
{{
  "name": "skill_name_snake_case",
  "description": "Skill 功能描述（一句话）",
  "skill_type": "atomic",
  "tags": ["tag1", "tag2"],
  "input_schema": {{
    "type": "object",
    "properties": {{
      "param1": {{"type": "string", "description": "参数描述"}}
    }},
    "required": ["param1"]
  }},
  "output_schema": {{
    "type": "object",
    "properties": {{
      "result": {{"type": "string"}}
    }}
  }},
  "prompt_template": "请执行以下操作：{{param1}}",
  "confidence": 0.85,
  "build_notes": "构建说明"
}}

只输出 JSON。
"""

_BUILD_FROM_TRAJECTORY_PROMPT = """
你是 SkillOS 的 Skill Builder Agent，负责从执行轨迹中提取 Skill。

## 执行轨迹
{trajectory}

## 要求
分析轨迹中的操作模式，提取可复用的 Skill。

## 输出格式（严格 JSON）
{{
  "name": "skill_name_snake_case",
  "description": "Skill 功能描述",
  "skill_type": "atomic",
  "tags": ["tag1"],
  "input_schema": {{"type": "object", "properties": {{}}, "required": []}},
  "output_schema": {{"type": "object", "properties": {{}}}},
  "prompt_template": "基于轨迹的执行模板",
  "confidence": 0.75,
  "build_notes": "从轨迹提取"
}}

只输出 JSON。
"""


class SkillBuilderAgent:
    """从任务/轨迹/文档生成 Skill 草稿。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def build_from_task(
        self, task_description: str, context: Optional[Dict[str, Any]] = None
    ) -> SkillDraft:
        """从任务描述生成 Skill。"""
        prompt = _BUILD_FROM_TASK_PROMPT.format(
            task_description=task_description,
            context=json.dumps(context or {}, ensure_ascii=False)[:200],
        )
        return self._build(prompt, "task", task_description)

    def build_from_trajectory(self, trajectory: str) -> SkillDraft:
        """从执行轨迹生成 Skill。"""
        prompt = _BUILD_FROM_TRAJECTORY_PROMPT.format(trajectory=trajectory[:800])
        return self._build(prompt, "trajectory", trajectory)

    def _build(self, prompt: str, source_type: str, raw_input: str) -> SkillDraft:
        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Skill Builder Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                skill_type_str = data.get("skill_type", "atomic")
                try:
                    skill_type = SkillType(skill_type_str)
                except ValueError:
                    skill_type = SkillType.ATOMIC

                skill = Skill(
                    name=data.get("name", "unnamed_skill"),
                    description=data.get("description", ""),
                    skill_type=skill_type,
                    tags=data.get("tags", []),
                    interface=SkillInterface(
                        input_schema=data.get("input_schema", {"type": "object", "properties": {}}),
                        output_schema=data.get("output_schema", {"type": "object", "properties": {}}),
                    ),
                    implementation=SkillImplementation(
                        prompt_template=data.get("prompt_template"),
                    ),
                    provenance=SkillProvenance(source_type=source_type, author="skill_builder"),
                )
                return SkillDraft(
                    skill=skill,
                    confidence=float(data.get("confidence", 0.7)),
                    source_type=source_type,
                    raw_input=raw_input[:200],
                    build_notes=data.get("build_notes", ""),
                )
        except Exception as exc:
            logger.warning(f"SkillBuilder LLM 调用失败: {exc}")

        # 降级：生成占位 Skill
        skill = Skill(
            name="unnamed_skill",
            description=f"从 {source_type} 自动生成（LLM 失败）",
            skill_type=SkillType.ATOMIC,
            tags=[source_type],
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {}},
            ),
            provenance=SkillProvenance(source_type=source_type, author="skill_builder"),
        )
        return SkillDraft(skill=skill, confidence=0.1, source_type=source_type, raw_input=raw_input[:200])

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None
