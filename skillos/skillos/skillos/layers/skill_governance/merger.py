"""Skill 合并/拆分器 — Meta Skill 的核心操作。

合并（Merge）：将两个语义相似的 Skill 合并为一个更通用的 Skill
拆分（Split）：将一个粒度过粗的 Skill 拆分为多个细粒度 Skill
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import (
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillType,
)
from ...models.graph_model import EdgeType, SkillEdge
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)

_MERGE_PROMPT = """
请将以下两个语义相似的 Skill 合并为一个更通用的 Skill。

## Skill A
名称: {name_a}
描述: {desc_a}
类型: {type_a}
接口: {interface_a}
实现: {impl_a}

## Skill B
名称: {name_b}
描述: {desc_b}
类型: {type_b}
接口: {interface_b}
实现: {impl_b}

## 合并策略
1. 新 Skill 名称应比两者更通用（如 fill_login_form + fill_register_form → fill_form）
2. 接口应兼容两者（参数取并集，可选参数用 default 处理）
3. 实现应能处理两者的场景
4. 保留两者的测试用例

## 输出格式（严格 JSON）
{{
  "merged_name": "通用名称",
  "merged_description": "合并后的描述",
  "merged_type": "atomic",
  "merged_domain": "web",
  "merged_granularity_level": 1,
  "merged_tags": ["tag1"],
  "merged_interface": {{
    "input_schema": {{}},
    "output_schema": {{}},
    "preconditions": [],
    "postconditions": [],
    "side_effects": []
  }},
  "merged_implementation": {{
    "language": "python",
    "code": null,
    "prompt_template": "合并后的 prompt",
    "tool_calls": [],
    "sub_skill_ids": []
  }},
  "merge_rationale": "合并理由"
}}

只输出 JSON，不要其他内容。
"""

_SPLIT_PROMPT = """
请将以下粒度过粗的 Skill 拆分为 2-4 个细粒度的子 Skill。

## 待拆分 Skill
名称: {name}
描述: {description}
类型: {skill_type}
粒度: {granularity_level}
接口: {interface}
实现: {implementation}

## 拆分原则
1. 每个子 Skill 应该是独立、可复用的原子操作
2. 子 Skill 组合后应能完成原 Skill 的功能
3. 每个子 Skill 的粒度级别应比原 Skill 低 1-2 级

## 输出格式（严格 JSON）
{{
  "sub_skills": [
    {{
      "name": "sub_skill_name",
      "description": "子 Skill 描述",
      "skill_type": "atomic",
      "granularity_level": 1,
      "domain": "web",
      "tags": [],
      "interface": {{
        "input_schema": {{}},
        "output_schema": {{}},
        "preconditions": [],
        "postconditions": []
      }},
      "implementation": {{
        "language": "python",
        "prompt_template": "执行描述"
      }}
    }}
  ],
  "split_rationale": "拆分理由",
  "composition_order": ["sub_skill_1", "sub_skill_2"]
}}

只输出 JSON，不要其他内容。
"""


@dataclass
class MergeResult:
    merged_skill: Optional[Skill] = None
    source_skill_ids: List[str] = field(default_factory=list)
    rationale: str = ""
    edges_to_create: List[SkillEdge] = field(default_factory=list)
    success: bool = False
    error: str = ""


@dataclass
class SplitResult:
    sub_skills: List[Skill] = field(default_factory=list)
    source_skill_id: str = ""
    rationale: str = ""
    composition_order: List[str] = field(default_factory=list)
    edges_to_create: List[SkillEdge] = field(default_factory=list)
    success: bool = False
    error: str = ""


class SkillMerger:
    """Skill 合并/拆分操作器。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def merge(self, skill_a: Skill, skill_b: Skill) -> MergeResult:
        """将两个 Skill 合并为一个更通用的 Skill。"""
        result = MergeResult(source_skill_ids=[skill_a.skill_id, skill_b.skill_id])

        prompt = _MERGE_PROMPT.format(
            name_a=skill_a.name,
            desc_a=skill_a.description,
            type_a=skill_a.skill_type.value,
            interface_a=json.dumps(skill_a.interface.model_dump(), ensure_ascii=False)[:600],
            impl_a=self._impl_summary(skill_a),
            name_b=skill_b.name,
            desc_b=skill_b.description,
            type_b=skill_b.skill_type.value,
            interface_b=json.dumps(skill_b.interface.model_dump(), ensure_ascii=False)[:600],
            impl_b=self._impl_summary(skill_b),
        )

        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的 Skill 合并专家，擅长将相似 Skill 抽象为更通用的版本。"
                "严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])

        data = self._extract_json(response.content)
        if not data:
            result.error = "LLM 返回无效响应"
            return result

        try:
            merged = self._build_skill_from_data(data, "merged")
            merged.provenance = SkillProvenance(
                source_type="merge",
                parent_skill_ids=[skill_a.skill_id, skill_b.skill_id],
                created_by_agent="skill_merger",
            )
            # 合并测试用例
            merged.test_cases = skill_a.test_cases + skill_b.test_cases
            # 合并外部引用
            merged.trajectory_refs = list(set(skill_a.trajectory_refs + skill_b.trajectory_refs))
            merged.tool_refs = list(set(skill_a.tool_refs + skill_b.tool_refs))

            result.merged_skill = merged
            result.rationale = data.get("merge_rationale", "")
            result.success = True

            # 创建 replaces 边
            for source_id in result.source_skill_ids:
                result.edges_to_create.append(SkillEdge(
                    source_id=merged.skill_id,
                    target_id=source_id,
                    edge_type=EdgeType.REPLACES,
                    weight=1.0,
                    description=f"合并替代: {result.rationale[:100]}",
                ))

            logger.info(
                f"Skill 合并成功: {skill_a.name} + {skill_b.name} → {merged.name}"
            )
        except Exception as e:
            result.error = str(e)
            logger.error(f"Skill 合并失败: {e}")

        return result

    async def split(self, skill: Skill) -> SplitResult:
        """将粒度过粗的 Skill 拆分为多个细粒度子 Skill。"""
        result = SplitResult(source_skill_id=skill.skill_id)

        prompt = _SPLIT_PROMPT.format(
            name=skill.name,
            description=skill.description,
            skill_type=skill.skill_type.value,
            granularity_level=skill.granularity_level,
            interface=json.dumps(skill.interface.model_dump(), ensure_ascii=False)[:600],
            implementation=self._impl_summary(skill),
        )

        response = self._llm.chat([
            Message.system(
                "你是 SkillOS 的 Skill 拆分专家，擅长将粗粒度 Skill 分解为细粒度原子操作。"
                "严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])

        data = self._extract_json(response.content)
        if not data or "sub_skills" not in data:
            result.error = "LLM 返回无效响应"
            return result

        try:
            sub_skills = []
            for sub_data in data["sub_skills"][:4]:
                sub = self._build_skill_from_data(sub_data, "split")
                sub.provenance = SkillProvenance(
                    source_type="split",
                    parent_skill_ids=[skill.skill_id],
                    created_by_agent="skill_merger",
                )
                sub_skills.append(sub)

            result.sub_skills = sub_skills
            result.rationale = data.get("split_rationale", "")
            result.composition_order = data.get("composition_order", [s.name for s in sub_skills])
            result.success = True

            # 创建 evolved_from 边（子 Skill 从原 Skill 演化而来）
            for sub in sub_skills:
                result.edges_to_create.append(SkillEdge(
                    source_id=sub.skill_id,
                    target_id=skill.skill_id,
                    edge_type=EdgeType.EVOLVED_FROM,
                    weight=1.0,
                    description=f"拆分自: {skill.name}",
                ))

            logger.info(
                f"Skill 拆分成功: {skill.name} → {len(sub_skills)} 个子 Skill"
            )
        except Exception as e:
            result.error = str(e)
            logger.error(f"Skill 拆分失败: {e}")

        return result

    def _build_skill_from_data(self, data: Dict[str, Any], source: str) -> Skill:
        """从 LLM 数据构建 Skill 对象。"""
        iface_data = data.get("interface", {})
        impl_data = data.get("implementation", {})

        interface = SkillInterface(
            input_schema=iface_data.get("input_schema", {"type": "object", "properties": {}}),
            output_schema=iface_data.get("output_schema", {"type": "object", "properties": {}}),
            preconditions=iface_data.get("preconditions", []),
            postconditions=iface_data.get("postconditions", []),
            side_effects=iface_data.get("side_effects", []),
        )

        implementation = None
        if impl_data:
            try:
                implementation = SkillImplementation(
                    language=impl_data.get("language", "python"),
                    code=impl_data.get("code"),
                    prompt_template=impl_data.get("prompt_template"),
                    tool_calls=impl_data.get("tool_calls", []),
                    sub_skill_ids=impl_data.get("sub_skill_ids", []),
                )
            except Exception:
                implementation = SkillImplementation(
                    prompt_template=data.get("description", "执行操作")
                )

        name_key = f"merged_name" if source == "merged" else "name"
        desc_key = f"merged_description" if source == "merged" else "description"
        type_key = f"merged_type" if source == "merged" else "skill_type"

        skill_type_str = data.get(type_key, "atomic")
        try:
            skill_type = SkillType(skill_type_str)
        except ValueError:
            skill_type = SkillType.ATOMIC

        return Skill(
            name=data.get(name_key, data.get("name", "unnamed_skill")),
            description=data.get(desc_key, data.get("description", "")),
            skill_type=skill_type,
            domain=data.get(f"merged_domain" if source == "merged" else "domain", "general"),
            granularity_level=data.get(
                f"merged_granularity_level" if source == "merged" else "granularity_level", 1
            ),
            state=SkillState.DRAFT,
            tags=data.get(f"merged_tags" if source == "merged" else "tags", []),
            interface=interface,
            implementation=implementation,
        )

    def _impl_summary(self, skill: Skill) -> str:
        if not skill.implementation:
            return "无实现"
        impl = skill.implementation
        if impl.code:
            return f"代码: {impl.code[:200]}"
        if impl.prompt_template:
            return f"Prompt: {impl.prompt_template[:200]}"
        if impl.sub_skill_ids:
            return f"组合子 Skill: {impl.sub_skill_ids}"
        return "无实现"

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
