"""Graph expansion over validated semantic relations."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from skillwiki._vendor.skillfabric.graph.models import Edge
from skillwiki._vendor.skillfabric.registry.models import SkillNode
from skillwiki._vendor.skillfabric.router.models import (
    ExpansionPath,
    ExpansionStep,
    RouterAlternative,
    RouterSkillCandidate,
)


@dataclass(frozen=True, slots=True)
class ExpansionResult:
    candidates: tuple[RouterSkillCandidate, ...]
    alternatives: tuple[RouterAlternative, ...]


def expand_semantic_candidates(
    seeds: list[RouterSkillCandidate],
    edges: list[Edge],
    skills: dict[str, SkillNode],
    *,
    max_depth: int,
    limit: int,
) -> ExpansionResult:
    """Expand all semantic relations under one deterministic candidate budget."""

    if max_depth < 0:
        raise ValueError("max_depth must be non-negative")
    if limit < len(seeds):
        raise ValueError("expanded_limit must be at least the number of retrieved seeds")
    seed_ids = [seed.skill_id for seed in seeds]
    if len(seed_ids) != len(set(seed_ids)):
        raise ValueError("retrieved seeds must be unique")
    if any(skill_id not in skills for skill_id in seed_ids):
        raise ValueError("retrieved seed is not present in the registry")

    adjacency: dict[str, list[tuple[str, Edge]]] = defaultdict(list)
    for edge in edges:
        if edge.source not in skills or edge.target not in skills:
            raise ValueError("graph edge references an unknown skill")
        if edge.type not in {"depend_on", "compose_with", "similar_to"}:
            raise ValueError(f"unsupported graph edge type: {edge.type}")
        adjacency[edge.source].append((edge.target, edge))
        adjacency[edge.target].append((edge.source, edge))
    for neighbors in adjacency.values():
        neighbors.sort(key=lambda item: (item[1].type, -item[1].confidence, item[0]))

    seed_set = set(seed_ids)
    expansions: dict[str, RouterSkillCandidate] = {}
    expansion_priorities: dict[str, tuple] = {}
    for seed in seeds:
        queue = deque([(seed.skill_id, ())])
        best_paths: dict[str, tuple] = {seed.skill_id: (0, -seed.score, ())}
        while queue:
            current, steps = queue.popleft()
            depth = len(steps)
            if depth >= max_depth:
                continue
            path_nodes = {seed.skill_id, *(step.target for step in steps)}
            for neighbor, edge in adjacency.get(current, []):
                if neighbor in path_nodes:
                    continue
                next_depth = depth + 1
                step = ExpansionStep(
                    source=current,
                    target=neighbor,
                    edge_type=edge.type,
                    semantic_source=edge.source,
                    semantic_target=edge.target,
                    confidence=edge.confidence,
                    reason=edge.reason,
                )
                next_steps = (*steps, step)
                score = _path_score(seed.score, next_steps)
                path_priority = (
                    next_depth,
                    -score,
                    _path_signature(next_steps),
                )
                previous_path = best_paths.get(neighbor)
                if previous_path is not None and previous_path <= path_priority:
                    continue
                best_paths[neighbor] = path_priority
                queue.append((neighbor, next_steps))
                if neighbor in seed_set:
                    continue
                skill = skills[neighbor]
                priority = (
                    next_depth,
                    -score,
                    seed.skill_id,
                    _path_signature(next_steps),
                )
                previous_priority = expansion_priorities.get(neighbor)
                if previous_priority is not None and previous_priority <= priority:
                    continue
                expansions[neighbor] = RouterSkillCandidate(
                    skill_id=skill.id,
                    name=skill.name,
                    score=score,
                    graph_depth=next_depth,
                    introduced_by=(ExpansionPath(seed_skill=seed.skill_id, steps=next_steps),),
                )
                expansion_priorities[neighbor] = priority

    expansion_rows = sorted(
        expansions.values(),
        key=lambda row: (*expansion_priorities[row.skill_id], row.skill_id),
    )
    selected = [*seeds, *expansion_rows[: limit - len(seeds)]]
    selected_ranks = {candidate.skill_id: rank for rank, candidate in enumerate(selected)}
    alternatives: dict[str, RouterAlternative] = {}
    alternative_priorities: dict[str, tuple[float, int, str]] = {}
    for edge in edges:
        if edge.type != "similar_to":
            continue
        source_rank = selected_ranks.get(edge.source)
        target_rank = selected_ranks.get(edge.target)
        if source_rank is None or target_rank is None:
            continue
        if source_rank < target_rank:
            selected_id, alternative_id = edge.source, edge.target
        else:
            selected_id, alternative_id = edge.target, edge.source
        candidate = RouterAlternative(
            skill_id=alternative_id,
            name=skills[alternative_id].name,
            alternative_to=selected_id,
            confidence=edge.confidence,
            reason=edge.reason,
        )
        priority = (-candidate.confidence, selected_ranks[selected_id], selected_id)
        existing_priority = alternative_priorities.get(alternative_id)
        if existing_priority is None or priority < existing_priority:
            alternatives[alternative_id] = candidate
            alternative_priorities[alternative_id] = priority
    ordered_alternatives = tuple(
        sorted(
            alternatives.values(),
            key=lambda row: (-row.confidence, row.skill_id, row.alternative_to),
        )
    )
    return ExpansionResult(candidates=tuple(selected), alternatives=ordered_alternatives)


def _path_score(seed_score: float, steps: tuple[ExpansionStep, ...]) -> float:
    weakest_edge = min(step.confidence * _step_transition_weight(step) for step in steps)
    return seed_score * weakest_edge / (len(steps) + 1)


def _transition_weights(edge_type: str) -> tuple[float, float]:
    if edge_type == "depend_on":
        return 1.0, 1.0
    if edge_type == "compose_with":
        return 0.7, 0.5
    if edge_type == "similar_to":
        return 0.3, 0.3
    raise ValueError(f"unsupported semantic edge type: {edge_type}")


def _step_transition_weight(step: ExpansionStep) -> float:
    forward, reverse = _transition_weights(step.edge_type)
    return forward if step.source == step.semantic_source else reverse


def _path_signature(steps: tuple[ExpansionStep, ...]) -> tuple[tuple[str, str, str], ...]:
    return tuple((step.source, step.target, step.edge_type) for step in steps)
