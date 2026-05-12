"""Runtime Skill composition into executable DAGs."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from ...models.skill_model import Skill
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger
from .retriever import SkillGroup

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
    """Executable Skill DAG."""

    graph_id: str = ""
    task_description: str = ""
    nodes: List[Skill] = field(default_factory=list)
    edges: List[SkillEdge] = field(default_factory=list)
    entry_skill_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def execution_order(self) -> List[str]:
        in_degree: Dict[str, int] = {node.skill_id: 0 for node in self.nodes}
        adj: Dict[str, List[str]] = {node.skill_id: [] for node in self.nodes}
        for edge in self.edges:
            if edge.edge_type == "parallel":
                continue
            if edge.source_id not in adj or edge.target_id not in in_degree:
                continue
            adj[edge.source_id].append(edge.target_id)
            in_degree[edge.target_id] += 1

        queue = [sid for sid, degree in in_degree.items() if degree == 0]
        order: List[str] = []
        while queue:
            current = queue.pop(0)
            order.append(current)
            for target in adj.get(current, []):
                in_degree[target] -= 1
                if in_degree[target] == 0:
                    queue.append(target)
        return order

    @property
    def parallel_groups(self) -> List[List[str]]:
        in_degree: Dict[str, int] = {node.skill_id: 0 for node in self.nodes}
        adj: Dict[str, List[str]] = {node.skill_id: [] for node in self.nodes}
        for edge in self.edges:
            if edge.edge_type == "parallel":
                continue
            if edge.source_id not in adj or edge.target_id not in in_degree:
                continue
            adj[edge.source_id].append(edge.target_id)
            in_degree[edge.target_id] += 1

        groups: List[List[str]] = []
        ready = [sid for sid, degree in in_degree.items() if degree == 0]
        seen: Set[str] = set()
        while ready:
            group = [sid for sid in ready if sid not in seen]
            if group:
                groups.append(group)
            next_ready: List[str] = []
            for sid in group:
                seen.add(sid)
                for target in adj.get(sid, []):
                    in_degree[target] -= 1
                    if in_degree[target] == 0:
                        next_ready.append(target)
            ready = next_ready
        return groups


_COMPOSE_PROMPT = """
You are the SkillOS Composition Agent. Compose listed skills into an executable DAG.

Task:
{task_description}

Available skills:
{skills_info}

Rules:
- Return JSON only.
- Use only listed skill ids.
- edge_type must be sequence, parallel, or conditional.
- Prefer parallel edges only for independent skills.
- Define data_mapping when a source output feeds a target input.

Return this JSON shape:
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
  "rationale": "composition rationale"
}}
"""


class CompositionAgent:
    """Compose retrieved Skills into a runtime SkillGraph."""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def compose(
        self,
        skills: List[Skill],
        task_description: str = "",
        skill_group: Optional[SkillGroup] = None,
    ) -> SkillGraph:
        skills = _filter_group_skills(skills, skill_group)
        graph = SkillGraph(
            graph_id=str(uuid.uuid4()),
            task_description=task_description,
            nodes=list(skills),
        )

        if not skills:
            return graph

        if len(skills) == 1:
            graph.entry_skill_id = skills[0].skill_id
            graph.metadata["composition_source"] = "single_skill"
            graph.metadata["parallel_groups"] = graph.parallel_groups
            return graph

        if skill_group:
            group_graph = _compose_from_skill_group(graph, skill_group)
            if group_graph:
                return group_graph

        skills_info = "\n".join(
            f"- [{skill.skill_id}] {skill.name}: {skill.description[:80]}"
            for skill in skills
        )
        prompt = _COMPOSE_PROMPT.format(
            task_description=task_description or "Execute all skills",
            skills_info=skills_info,
        )

        try:
            response = self._llm.chat([
                Message.system(
                    "You are the SkillOS Composition Agent. Return strict JSON only."
                ),
                Message.user(prompt),
            ])
            data = self._extract_json(response.content)
            if data:
                graph.entry_skill_id = str(data.get("entry_skill_id", skills[0].skill_id))
                graph.metadata["rationale"] = str(data.get("rationale", ""))
                for edge in data.get("edges", []):
                    if not isinstance(edge, dict):
                        continue
                    graph.edges.append(SkillEdge(
                        source_id=str(edge.get("source_id", "")),
                        target_id=str(edge.get("target_id", "")),
                        edge_type=str(edge.get("edge_type", "sequence")),
                        data_mapping=edge.get("data_mapping", {})
                        if isinstance(edge.get("data_mapping", {}), dict)
                        else {},
                    ))
                graph = _sanitize_graph(graph)
                if graph.edges:
                    graph.metadata["composition_source"] = "llm"
                    return graph
        except Exception as exc:
            logger.warning("Composition LLM failed; using fallback DAG: %s", exc)

        schema_graph = _compose_from_schema(graph)
        if schema_graph.edges:
            return schema_graph

        graph.entry_skill_id = skills[0].skill_id
        for index in range(len(skills) - 1):
            graph.edges.append(SkillEdge(
                source_id=skills[index].skill_id,
                target_id=skills[index + 1].skill_id,
                edge_type="sequence",
            ))
        graph.metadata["composition_source"] = "sequential_fallback"
        return _sanitize_graph(graph)

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


def _filter_group_skills(skills: List[Skill], skill_group: Optional[SkillGroup]) -> List[Skill]:
    if not skill_group:
        return list(skills)
    avoid = set(skill_group.avoid_skill_ids)
    preferred = skill_group.ordered_ids()
    skill_map = {skill.skill_id: skill for skill in skills if skill.skill_id not in avoid}
    ordered = [skill_map[skill_id] for skill_id in preferred if skill_id in skill_map]
    for skill in skills:
        if skill.skill_id not in avoid and skill not in ordered:
            ordered.append(skill)
    return ordered


def _compose_from_skill_group(graph: SkillGraph, skill_group: SkillGroup) -> Optional[SkillGraph]:
    skill_ids = {skill.skill_id for skill in graph.nodes}
    support = [sid for sid in skill_group.support_skill_ids if sid in skill_ids]
    starts = [sid for sid in skill_group.start_skill_ids if sid in skill_ids]
    checks = [sid for sid in skill_group.check_skill_ids if sid in skill_ids]
    if not (support or starts or checks):
        return None

    graph.entry_skill_id = starts[0] if starts else (support[0] if support else checks[0])
    edges: List[SkillEdge] = []
    for source in support:
        for target in starts:
            edges.append(SkillEdge(source_id=source, target_id=target))
    for source in starts or support:
        for target in checks:
            edges.append(SkillEdge(source_id=source, target_id=target))
    graph.edges = edges
    graph.metadata["composition_source"] = "skill_group"
    graph.metadata["group_rationale"] = skill_group.rationale
    return _sanitize_graph(graph)


def _compose_from_schema(graph: SkillGraph) -> SkillGraph:
    edges: List[SkillEdge] = []
    for source in graph.nodes:
        source_outputs = _schema_keys(source.interface.output_schema)
        if not source_outputs:
            continue
        for target in graph.nodes:
            if source.skill_id == target.skill_id:
                continue
            target_inputs = _schema_keys(target.interface.input_schema)
            shared = source_outputs & target_inputs
            if shared:
                edges.append(SkillEdge(
                    source_id=source.skill_id,
                    target_id=target.skill_id,
                    data_mapping={key: key for key in sorted(shared)},
                ))
    graph.edges = edges
    graph.entry_skill_id = graph.execution_order[0] if graph.execution_order else ""
    graph.metadata["composition_source"] = "schema_fallback"
    return _sanitize_graph(graph)


def _sanitize_graph(graph: SkillGraph) -> SkillGraph:
    node_ids = {node.skill_id for node in graph.nodes}
    clean_edges: List[SkillEdge] = []
    seen: Set[tuple[str, str, str]] = set()
    for edge in graph.edges:
        if edge.source_id not in node_ids or edge.target_id not in node_ids:
            continue
        if edge.source_id == edge.target_id:
            continue
        edge_type = edge.edge_type if edge.edge_type in {"sequence", "parallel", "conditional"} else "sequence"
        key = (edge.source_id, edge.target_id, edge_type)
        if key in seen:
            continue
        seen.add(key)
        clean_edges.append(SkillEdge(
            source_id=edge.source_id,
            target_id=edge.target_id,
            edge_type=edge_type,
            condition=edge.condition,
            data_mapping=edge.data_mapping if isinstance(edge.data_mapping, dict) else {},
        ))
    graph.edges = clean_edges
    if graph.entry_skill_id not in node_ids:
        order = graph.execution_order
        graph.entry_skill_id = order[0] if order else (graph.nodes[0].skill_id if graph.nodes else "")
    if len(graph.execution_order) != len(graph.nodes):
        graph.edges = _break_cycles(graph)
    graph.metadata["parallel_groups"] = graph.parallel_groups
    return graph


def _break_cycles(graph: SkillGraph) -> List[SkillEdge]:
    order = {node.skill_id: index for index, node in enumerate(graph.nodes)}
    return [
        edge for edge in graph.edges
        if order.get(edge.source_id, -1) < order.get(edge.target_id, -1)
    ]


def _schema_keys(schema: Any) -> Set[str]:
    if not isinstance(schema, dict):
        return set()
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return set()
    return {str(key) for key in properties}
