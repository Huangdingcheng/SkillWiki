"""候选 Skill 挖掘器 — 从 ExperienceUnit 中识别 Skill 候选。

职责：
1. 分析 ExperienceUnit，识别可复用的操作模式
2. 生成 SkillProposal（S1 状态）
3. 检测与已有 Skill 的相似性，决定是新建还是合并
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from ...models.experience_model import ExperienceUnit, SkillProposal
from ...models.skill_model import SkillType
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger

logger = get_logger(__name__)

_CANDIDATE_MINE_PROMPT = """
请分析以下经验单元，识别其中可以封装为可复用 Skill 的操作模式。

## 经验单元
标题: {title}
描述: {description}
任务: {task_description}
领域: {domain}
步骤数: {step_count}
步骤摘要:
{steps_summary}

## 任务
识别这个经验单元中包含的 Skill 候选（可能有 1-3 个）。
对每个候选 Skill 判断：
1. 是否值得封装为独立 Skill（复用价值高、边界清晰）
2. Skill 类型：atomic（单一操作）/ composite（多步骤组合）/ meta（管理其他 Skill）
3. 粒度级别：1（最细粒度）到 5（高层策略）
4. 建议名称（snake_case）
5. 输入/输出接口草案

## 输出格式（严格 JSON）
{{
  "candidates": [
    {{
      "proposed_name": "skill_name",
      "proposed_description": "功能描述",
      "proposed_type": "atomic",
      "granularity_level": 1,
      "proposed_domain": "web",
      "proposed_tags": ["tag1"],
      "input_schema_draft": {{
        "type": "object",
        "properties": {{}},
        "required": []
      }},
      "output_schema_draft": {{
        "type": "object",
        "properties": {{}}
      }},
      "preconditions_draft": ["条件1"],
      "postconditions_draft": ["结果1"],
      "confidence": 0.85,
      "reuse_value": "high",
      "reason": "封装理由"
    }}
  ],
  "skip_reason": null
}}

如果整个经验单元不值得封装，设置 candidates 为空数组，skip_reason 说明原因。
只输出 JSON，不要其他内容。
"""

_SIMILARITY_CHECK_PROMPT = """
请判断以下两个 Skill 描述是否语义相似，是否应该合并。

## Skill A（候选）
名称: {name_a}
描述: {desc_a}
类型: {type_a}
领域: {domain_a}

## Skill B（已有）
名称: {name_b}
描述: {desc_b}
类型: {type_b}
领域: {domain_b}

## 判断标准
- 相似度 > 0.9：几乎相同，应该合并
- 相似度 0.7-0.9：高度相似，建议合并
- 相似度 0.5-0.7：部分相似，可能是特化/泛化关系
- 相似度 < 0.5：不同 Skill

## 输出格式（严格 JSON）
{{
  "similarity": 0.85,
  "relationship": "same|specialization|generalization|partial|different",
  "should_merge": true,
  "reason": "判断理由"
}}

只输出 JSON，不要其他内容。
"""


class CandidateMiner:
    """从经验单元中挖掘 Skill 候选。"""

    def __init__(
        self,
        llm_client: LLMClient,
        min_confidence: float = 0.6,
        max_candidates_per_unit: int = 3,
    ) -> None:
        self._llm = llm_client
        self._min_confidence = min_confidence
        self._max_candidates = max_candidates_per_unit

    async def mine(
        self,
        experience: ExperienceUnit,
        existing_skill_summaries: Optional[List[Dict[str, Any]]] = None,
    ) -> List[SkillProposal]:
        """从单个经验单元中挖掘 Skill 候选。

        Args:
            experience: 输入经验单元
            existing_skill_summaries: 已有 Skill 的摘要列表（用于相似性检测）

        Returns:
            SkillProposal 列表（S1 状态）
        """
        # 1. LLM 识别候选
        raw_candidates = await self._identify_candidates(experience)
        if not raw_candidates:
            logger.debug(f"经验单元 {experience.experience_id[:8]} 无候选 Skill")
            return []

        # 2. 过滤低置信度候选
        filtered = [
            c for c in raw_candidates
            if c.get("confidence", 0) >= self._min_confidence
        ][: self._max_candidates]

        # 3. 构建 SkillProposal
        proposals = []
        for candidate in filtered:
            proposal = SkillProposal(
                source_experience_id=experience.experience_id,
                proposed_name=candidate["proposed_name"],
                proposed_description=candidate.get("proposed_description", ""),
                proposed_type=candidate.get("proposed_type", "atomic"),
                proposed_domain=candidate.get("proposed_domain", experience.domain),
                proposed_tags=candidate.get("proposed_tags", []),
                input_schema_draft=candidate.get("input_schema_draft", {}),
                output_schema_draft=candidate.get("output_schema_draft", {}),
                preconditions_draft=candidate.get("preconditions_draft", []),
                postconditions_draft=candidate.get("postconditions_draft", []),
                confidence=candidate.get("confidence", 0.7),
            )

            # 4. 相似性检测
            if existing_skill_summaries:
                similar = await self._find_similar(proposal, existing_skill_summaries)
                if similar:
                    proposal.similar_skill_ids = [s["skill_id"] for s in similar]
                    proposal.similarity_scores = {
                        s["skill_id"]: s["similarity"] for s in similar
                    }

            proposals.append(proposal)
            logger.info(
                f"候选 Skill: {proposal.proposed_name} "
                f"(置信度={proposal.confidence:.2f}, 相似={len(proposal.similar_skill_ids)})"
            )

        return proposals

    async def mine_batch(
        self,
        experiences: List[ExperienceUnit],
        existing_skill_summaries: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[SkillProposal]]:
        """批量挖掘，返回 {experience_id: [proposals]} 映射。"""
        result: Dict[str, List[SkillProposal]] = {}
        for exp in experiences:
            proposals = await self.mine(exp, existing_skill_summaries)
            result[exp.experience_id] = proposals
        return result

    async def _identify_candidates(
        self, experience: ExperienceUnit
    ) -> List[Dict[str, Any]]:
        """调用 LLM 识别候选 Skill。"""
        steps_summary = self._summarize_steps(experience)
        prompt = _CANDIDATE_MINE_PROMPT.format(
            title=experience.title or "无标题",
            description=experience.description or "无描述",
            task_description=experience.task_description or "未知任务",
            domain=experience.domain,
            step_count=experience.step_count,
            steps_summary=steps_summary,
        )

        from ...utils.llm_client import Message
        messages = [
            Message.system(
                "你是 SkillOS 的 Skill 挖掘专家，擅长从操作经验中识别可复用的操作模式。"
                "严格按照 JSON 格式输出。"
            ),
            Message.user(prompt),
        ]
        response = self._llm.chat(messages)
        data = self._extract_json(response.content)

        if not data:
            return []
        return data.get("candidates", [])

    async def _find_similar(
        self,
        proposal: SkillProposal,
        existing_summaries: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """检测与已有 Skill 的相似性。"""
        similar = []
        # 先做快速名称/标签过滤，减少 LLM 调用
        candidates = self._quick_filter(proposal, existing_summaries)

        for existing in candidates[:5]:  # 最多检查 5 个
            sim_result = await self._llm_similarity_check(proposal, existing)
            if sim_result and sim_result.get("similarity", 0) >= 0.7:
                similar.append({
                    "skill_id": existing["skill_id"],
                    "similarity": sim_result["similarity"],
                    "relationship": sim_result.get("relationship", "similar"),
                    "should_merge": sim_result.get("should_merge", False),
                })

        similar.sort(key=lambda x: x["similarity"], reverse=True)
        return similar

    def _quick_filter(
        self,
        proposal: SkillProposal,
        existing_summaries: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """快速过滤：同领域 + 名称/标签有重叠的 Skill。"""
        proposal_tokens = set(re.split(r"[\s_\-]+", proposal.proposed_name.lower()))
        proposal_tags = set(proposal.proposed_tags)

        scored = []
        for existing in existing_summaries:
            if existing.get("domain") != proposal.proposed_domain:
                continue
            existing_tokens = set(re.split(r"[\s_\-]+", existing.get("name", "").lower()))
            existing_tags = set(existing.get("tags", []))
            token_overlap = len(proposal_tokens & existing_tokens)
            tag_overlap = len(proposal_tags & existing_tags)
            if token_overlap > 0 or tag_overlap > 0:
                scored.append((existing, token_overlap + tag_overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [e for e, _ in scored]

    async def _llm_similarity_check(
        self,
        proposal: SkillProposal,
        existing: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """使用 LLM 判断两个 Skill 的相似度。"""
        prompt = _SIMILARITY_CHECK_PROMPT.format(
            name_a=proposal.proposed_name,
            desc_a=proposal.proposed_description,
            type_a=proposal.proposed_type,
            domain_a=proposal.proposed_domain,
            name_b=existing.get("name", ""),
            desc_b=existing.get("description", ""),
            type_b=existing.get("skill_type", ""),
            domain_b=existing.get("domain", ""),
        )
        from ...utils.llm_client import Message
        response = self._llm.chat([Message.user(prompt)])
        return self._extract_json(response.content)

    def _summarize_steps(self, experience: ExperienceUnit) -> str:
        """生成步骤摘要（避免超出 token 限制）。"""
        if not experience.steps:
            return experience.raw_content[:500] if experience.raw_content else "无步骤"
        lines = []
        for step in experience.steps[:15]:  # 最多 15 步
            line = f"  {step.step_index}. {step.action_type}"
            if step.action_target:
                line += f" → {step.action_target[:50]}"
            if step.action_value:
                line += f" = {str(step.action_value)[:30]}"
            lines.append(line)
        if len(experience.steps) > 15:
            lines.append(f"  ... 共 {len(experience.steps)} 步")
        return "\n".join(lines)

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
