"""Skill 形式化生成器 — 从 SkillProposal 生成完整的 Draft Skill（S2 状态）。

职责：
1. 将 SkillProposal 扩展为完整的 Skill 对象
2. 生成精确的 JSON Schema 接口规范
3. 生成可执行的实现代码或 prompt 模板
4. 生成测试用例
5. 判断粒度级别和 Skill 类型
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceUnit, SkillProposal
from ...models.skill_model import (
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillTestCase,
    SkillType,
)
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger

logger = get_logger(__name__)

_SKILL_GENERATE_PROMPT = """
请根据以下 Skill 候选提案，生成一个完整的 Skill 定义。

## Skill 候选提案
名称: {proposed_name}
描述: {proposed_description}
类型: {proposed_type}
领域: {proposed_domain}
标签: {proposed_tags}
输入草案: {input_schema_draft}
输出草案: {output_schema_draft}
前置条件: {preconditions}
后置条件: {postconditions}

## 来源经验
{experience_summary}

## 任务
生成完整的 Skill 定义，包括：
1. 精确的 JSON Schema 接口规范（input_schema, output_schema）
2. 前置/后置条件（自然语言）
3. 副作用列表
4. 实现方式（code 或 prompt_template）
5. 3-5 个测试用例
6. 粒度级别（1-5）

## 输出格式（严格 JSON）
{{
  "name": "skill_name",
  "version": "1.0.0",
  "display_name": "Human Readable Name",
  "description": "详细功能描述",
  "skill_type": "atomic",
  "domain": "web",
  "granularity_level": 1,
  "tags": ["tag1", "tag2"],
  "interface": {{
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
        "result": {{"type": "boolean"}}
      }}
    }},
    "preconditions": ["条件1"],
    "postconditions": ["结果1"],
    "side_effects": []
  }},
  "implementation": {{
    "language": "python",
    "code": "# 实现代码\\n...",
    "prompt_template": null,
    "tool_calls": ["tool_name"],
    "sub_skill_ids": []
  }},
  "test_cases": [
    {{
      "name": "test_basic",
      "description": "基础功能测试",
      "input_data": {{"param1": "value1"}},
      "expected_output": {{"result": true}},
      "expected_state_changes": {{}},
      "tags": ["basic"]
    }}
  ]
}}

只输出 JSON，不要其他内容。
"""

_COMPOSITE_SKILL_PROMPT = """
请根据以下信息，生成一个组合 Skill 的定义。

## 组合 Skill 候选
名称: {proposed_name}
描述: {proposed_description}
领域: {proposed_domain}

## 可用的子 Skill
{sub_skills_info}

## 任务
1. 确定哪些子 Skill 应该被组合
2. 定义执行顺序（支持顺序/并行）
3. 定义组合 Skill 的接口（输入/输出）
4. 生成测试用例

## 输出格式（严格 JSON）
{{
  "name": "functional_skill_name",
  "description": "功能 Skill 描述",
  "skill_type": "functional",
  "granularity_level": 2,
  "interface": {{
    "input_schema": {{}},
    "output_schema": {{}},
    "preconditions": [],
    "postconditions": []
  }},
  "implementation": {{
    "language": "python",
    "sub_skill_ids": ["id1", "id2"],
    "execution_order": ["id1", "id2"],
    "prompt_template": "执行步骤描述"
  }},
  "test_cases": []
}}

只输出 JSON，不要其他内容。
"""


class SkillFormalizer:
    """将 SkillProposal 形式化为完整的 Draft Skill。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    async def formalize(
        self,
        proposal: SkillProposal,
        experience: Optional[ExperienceUnit] = None,
        sub_skills: Optional[List[Skill]] = None,
    ) -> Skill:
        """将提案形式化为 Draft Skill。

        Args:
            proposal: Skill 候选提案
            experience: 来源经验单元（可选，提供更多上下文）
            sub_skills: 可用的子 Skill 列表（用于 composite 类型）

        Returns:
            Draft Skill（S2 状态）
        """
        skill_type = SkillType(proposal.proposed_type)

        if skill_type == SkillType.FUNCTIONAL and sub_skills:
            skill_data = await self._generate_composite(proposal, sub_skills)
        else:
            skill_data = await self._generate_atomic_or_meta(proposal, experience)

        if not skill_data:
            # LLM 失败时，用提案数据构建最小 Skill
            skill_data = self._build_minimal_from_proposal(proposal)

        skill = self._build_skill(skill_data, proposal)
        logger.info(f"Draft Skill 已生成: {skill.name} v{skill.version}")
        return skill

    async def formalize_batch(
        self,
        proposals: List[SkillProposal],
        experiences: Optional[Dict[str, ExperienceUnit]] = None,
    ) -> List[Skill]:
        """批量形式化。"""
        skills = []
        for proposal in proposals:
            exp = experiences.get(proposal.source_experience_id) if experiences else None
            skill = await self.formalize(proposal, exp)
            skills.append(skill)
        return skills

    async def _generate_atomic_or_meta(
        self,
        proposal: SkillProposal,
        experience: Optional[ExperienceUnit],
    ) -> Optional[Dict[str, Any]]:
        """生成 Atomic 或 Meta Skill。"""
        exp_summary = ""
        if experience:
            exp_summary = self._summarize_experience(experience)

        prompt = _SKILL_GENERATE_PROMPT.format(
            proposed_name=proposal.proposed_name,
            proposed_description=proposal.proposed_description,
            proposed_type=proposal.proposed_type,
            proposed_domain=proposal.proposed_domain,
            proposed_tags=json.dumps(proposal.proposed_tags, ensure_ascii=False),
            input_schema_draft=json.dumps(proposal.input_schema_draft, ensure_ascii=False),
            output_schema_draft=json.dumps(proposal.output_schema_draft, ensure_ascii=False),
            preconditions=json.dumps(proposal.preconditions_draft, ensure_ascii=False),
            postconditions=json.dumps(proposal.postconditions_draft, ensure_ascii=False),
            experience_summary=exp_summary or "无来源经验",
        )

        from ...utils.llm_client import Message
        response = self._llm.chat([
            Message.system(
                "你是 SkillWiki 的 Skill 设计专家，擅长设计清晰、可复用的 Skill 接口。"
                "严格按照 JSON 格式输出，确保 JSON Schema 合法。"
            ),
            Message.user(prompt),
        ])
        return self._extract_json(response.content)

    async def _generate_composite(
        self,
        proposal: SkillProposal,
        sub_skills: List[Skill],
    ) -> Optional[Dict[str, Any]]:
        """生成 Composite Skill。"""
        sub_skills_info = "\n".join([
            f"- {s.skill_id}: {s.name} ({s.description[:80]})"
            for s in sub_skills[:10]
        ])

        prompt = _COMPOSITE_SKILL_PROMPT.format(
            proposed_name=proposal.proposed_name,
            proposed_description=proposal.proposed_description,
            proposed_domain=proposal.proposed_domain,
            sub_skills_info=sub_skills_info,
        )

        from ...utils.llm_client import Message
        response = self._llm.chat([
            Message.system(
                "你是 SkillWiki 的 Skill 组合专家，擅长设计组合 Skill 的执行流程。"
                "严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ])
        data = self._extract_json(response.content)
        if data:
            # 填充真实的 sub_skill_ids
            data.setdefault("implementation", {})
            data["implementation"]["sub_skill_ids"] = [s.skill_id for s in sub_skills[:5]]
        return data

    def _build_skill(
        self,
        skill_data: Dict[str, Any],
        proposal: SkillProposal,
    ) -> Skill:
        """从 LLM 生成的数据构建 Skill 对象。"""
        iface_data = skill_data.get("interface", {})
        impl_data = skill_data.get("implementation", {})
        test_cases_data = skill_data.get("test_cases", [])

        interface = SkillInterface(
            input_schema=iface_data.get("input_schema", proposal.input_schema_draft),
            output_schema=iface_data.get("output_schema", proposal.output_schema_draft),
            preconditions=iface_data.get("preconditions", proposal.preconditions_draft),
            postconditions=iface_data.get("postconditions", proposal.postconditions_draft),
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
                    execution_order=impl_data.get("execution_order"),
                )
            except Exception as e:
                logger.warning(f"实现构建失败，使用 prompt 模板: {e}")
                implementation = SkillImplementation(
                    prompt_template=proposal.proposed_description
                )

        test_cases = []
        for tc_data in test_cases_data[:5]:
            try:
                tc = SkillTestCase(
                    name=tc_data.get("name", "test"),
                    description=tc_data.get("description", ""),
                    input_data=tc_data.get("input_data", {}),
                    expected_output=tc_data.get("expected_output"),
                    expected_state_changes=tc_data.get("expected_state_changes", {}),
                    tags=tc_data.get("tags", []),
                )
                test_cases.append(tc)
            except Exception:
                pass

        provenance = SkillProvenance(
            source_type="trajectory" if proposal.source_experience_id else "manual",
            source_ids=[proposal.source_experience_id],
            created_by_agent="skill_formalizer",
        )

        skill_type_str = skill_data.get("skill_type", proposal.proposed_type)
        try:
            skill_type = SkillType(skill_type_str)
        except ValueError:
            skill_type = SkillType.ATOMIC

        return Skill(
            name=skill_data.get("name", proposal.proposed_name),
            version=skill_data.get("version", "1.0.0"),
            display_name=skill_data.get("display_name", ""),
            description=skill_data.get("description", proposal.proposed_description),
            skill_type=skill_type,
            domain=skill_data.get("domain", proposal.proposed_domain),
            granularity_level=skill_data.get("granularity_level", 1),
            state=SkillState.DRAFT,
            tags=skill_data.get("tags", proposal.proposed_tags),
            interface=interface,
            implementation=implementation,
            test_cases=test_cases,
            trajectory_refs=[proposal.source_experience_id],
            provenance=provenance,
        )

    def _build_minimal_from_proposal(self, proposal: SkillProposal) -> Dict[str, Any]:
        """LLM 失败时的降级方案。"""
        return {
            "name": proposal.proposed_name,
            "description": proposal.proposed_description,
            "skill_type": proposal.proposed_type,
            "domain": proposal.proposed_domain,
            "granularity_level": 1,
            "tags": proposal.proposed_tags,
            "interface": {
                "input_schema": proposal.input_schema_draft,
                "output_schema": proposal.output_schema_draft,
                "preconditions": proposal.preconditions_draft,
                "postconditions": proposal.postconditions_draft,
            },
            "implementation": {
                "prompt_template": proposal.proposed_description,
            },
            "test_cases": [],
        }

    def _summarize_experience(self, experience: ExperienceUnit) -> str:
        parts = [f"任务: {experience.task_description or experience.title}"]
        if experience.steps:
            parts.append(f"步骤数: {len(experience.steps)}")
            for step in experience.steps[:5]:
                parts.append(f"  - {step.action_type}: {step.action_target or ''}")
        elif experience.raw_content:
            parts.append(f"内容摘要: {experience.raw_content[:300]}")
        return "\n".join(parts)

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
