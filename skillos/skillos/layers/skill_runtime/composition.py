"""Composition Agent — 将检索到的 Skill 组合为可执行的 Skill Graph。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SkillEdge:
    source_id: str
    target_id: str
    edge_type: str = "sequence"  # sequence | parallel | conditional
    condition: Optional[str] = None
    data_mapping: Dict[str, str] = field(default_factory=dict)


@dataclass
class SkillGraph:
    """可执行的 Skill 组合图。"""
    graph_id: str = ""
    task_description: str = ""
    nodes: List[Skill] = field(default_factory=list)
    edges: List[SkillEdge] = field(default_factory=list)
    entry_skill_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def execution_order(self) -> List[str]:
        """拓扑排序后的执行顺序（skill_id 列表）。"""
        in_degree: Dict[str, int] = {n.skill_id: 0 for n in self.nodes}
        adj: Dict[str, List[str]] = {n.skill_id: [] for n in self.nodes}
        for e in self.edges:
            if e.edge_type != "parallel":
                adj[e.source_id].append(e.target_id)
                in_degree[e.target_id] = in_degree.get(e.target_id, 0) + 1
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order = []
        while queue:
            cur = queue.pop(0)
            order.append(cur)
            for nxt in adj.get(cur, []):
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)
        return order


_COMPOSE_PROMPT = """
你是 SkillOS 的 Composition Agent，负责将一组 Skill 组合为最优执行图。

## 任务
{task_description}

## 可用 Skill
{skills_info}

## 要求
1. 确定 Skill 的执行顺序和依赖关系
2. 识别可以并行执行的 Skill
3. 定义 Skill 间的数据流映射
4. 选择合适的入口 Skill

## 输出格式（严格 JSON）
{{
  "entry_skill_id": "skill_id",
  "edges": [
    {{
      "source_id": "skill_id_1",
      "target_id": "skill_id_2",
      "edge_type": "sequence",
      "data_mapping": {{"target_param": "source_output_field"}}
    }}
  ],
  "rationale": "组合理由"
}}

只输出 JSON。
"""


class CompositionAgent:
    """将检索到的 Skill 组合为可执行的 Skill Graph。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def compose(self, skills: List[Skill], task_description: str = "") -> SkillGraph:
        """将 Skill 列表组合为 SkillGraph。"""
        import uuid
        graph = SkillGraph(
            graph_id=str(uuid.uuid4()),
            task_description=task_description,
            nodes=list(skills),
        )

        if not skills:
            return graph

        if len(skills) == 1:
            graph.entry_skill_id = skills[0].skill_id
            return graph

        skills_info = "\n".join(
            f"- [{s.skill_id}] {s.name}: {s.description[:80]}"
            for s in skills
        )
        prompt = _COMPOSE_PROMPT.format(
            task_description=task_description or "执行所有 Skill",
            skills_info=skills_info,
        )

        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Composition Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                graph.entry_skill_id = data.get("entry_skill_id", skills[0].skill_id)
                graph.metadata["rationale"] = data.get("rationale", "")
                for e in data.get("edges", []):
                    graph.edges.append(SkillEdge(
                        source_id=e.get("source_id", ""),
                        target_id=e.get("target_id", ""),
                        edge_type=e.get("edge_type", "sequence"),
                        data_mapping=e.get("data_mapping", {}),
                    ))
                return graph
        except Exception as exc:
            logger.warning(f"Composition LLM 调用失败，使用顺序组合: {exc}")

        # 降级：顺序链接
        graph.entry_skill_id = skills[0].skill_id
        for i in range(len(skills) - 1):
            graph.edges.append(SkillEdge(
                source_id=skills[i].skill_id,
                target_id=skills[i + 1].skill_id,
                edge_type="sequence",
            ))
        return graph

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
