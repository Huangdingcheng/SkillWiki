"""Skill search and ranking utilities."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from ...models.skill_model import Skill, SkillState, SkillType, SkillVisibility
from ...storage.postgres_db import PostgresConnection, SkillRepository
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """Search result entry."""

    skill: Skill
    score: float
    match_reasons: List[str] = field(default_factory=list)

    def __lt__(self, other: "SearchResult") -> bool:
        return _result_sort_key(self) < _result_sort_key(other)


@dataclass
class SearchQuery:
    """Structured Skill search query."""

    text: str = ""
    tags: List[str] = field(default_factory=list)
    skill_type: Optional[SkillType] = None
    domain: Optional[str] = None
    state: Optional[SkillState] = None
    min_success_rate: float = 0.0
    max_results: int = 20
    include_deprecated: bool = False
    visibility: str = SkillVisibility.USER.value


class SkillSearchEngine:
    """Repository-backed search engine using the shared rule-based scorer."""

    def __init__(self, pg_conn: PostgresConnection) -> None:
        self._repo = SkillRepository(pg_conn)

    async def search(self, query: SearchQuery) -> List[SearchResult]:
        filters: Dict[str, Any] = {}
        if query.skill_type:
            filters["skill_type"] = query.skill_type.value
        if query.domain:
            filters["domain"] = query.domain
        if query.state:
            filters["state"] = query.state.value

        candidates: List[Skill] = []
        if query.text:
            candidates.extend(await self._repo.list(
                filters={**filters, "name_like": query.text.replace(" ", "_")},
                limit=query.max_results * 5,
            ))
            candidates.extend(await self._repo.list(
                filters={**filters, "name_like": query.text},
                limit=query.max_results * 5,
            ))
            known_ids = {skill.skill_id for skill in candidates}
            for skill in await self._repo.list(filters=filters, limit=query.max_results * 20):
                if skill.skill_id not in known_ids:
                    candidates.append(skill)
                    known_ids.add(skill.skill_id)
        if query.tags:
            known_ids = {skill.skill_id for skill in candidates}
            for skill in await self._repo.search_by_tags(query.tags, limit=query.max_results * 5):
                if skill.skill_id not in known_ids:
                    candidates.append(skill)
                    known_ids.add(skill.skill_id)
        if not candidates:
            candidates = await self._repo.list(filters=filters, limit=query.max_results * 5)

        return rank_search_results(candidates, query)

    async def search_text(self, text: str, limit: int = 10) -> List[SearchResult]:
        return await self.search(SearchQuery(text=text, max_results=limit))

    def _score(self, skill: Skill, query: SearchQuery) -> SearchResult:
        """Compatibility wrapper for existing tests and callers."""

        return score_skill_match(skill, query)

    def _text_relevance(self, skill: Skill, query_text: str) -> float:
        """Return text-only relevance for legacy tests."""

        normalized_query = normalize_text(query_text)
        if not normalized_query:
            return 0.0

        normalized_name = normalize_text(skill.name)
        normalized_display_name = normalize_text(skill.display_name)
        if normalized_query in {normalized_name, normalized_display_name}:
            return 1.0

        query_tokens = set(extract_query_tokens(query_text))
        if not query_tokens:
            return 0.0

        name_tokens = set(extract_query_tokens(f"{skill.name} {skill.display_name}"))
        name_overlap = query_tokens & name_tokens
        if name_overlap:
            return 0.5 + 0.4 * len(name_overlap) / len(query_tokens)

        description_tokens = set(extract_query_tokens(skill.description))
        desc_overlap = query_tokens & description_tokens
        if desc_overlap:
            return 0.4 * len(desc_overlap) / len(query_tokens)

        tag_overlap = query_tokens & normalize_tags(skill.tags)
        if tag_overlap:
            return 0.3 * len(tag_overlap) / len(query_tokens)

        return 0.0

    def _extract_keywords(self, text: str) -> List[str]:
        """Compatibility wrapper around the shared tokenizer."""

        keywords = extract_query_tokens(text)
        keywords.sort(key=len, reverse=True)
        return keywords

    async def find_by_capability(
        self,
        capability_description: str,
        domain: Optional[str] = None,
    ) -> List[SearchResult]:
        keywords = extract_query_tokens(capability_description)
        results: List[SearchResult] = []
        seen: Set[str] = set()
        for keyword in keywords[:5]:
            for result in await self.search(SearchQuery(
                text=keyword,
                domain=domain,
                max_results=10,
            )):
                if result.skill.skill_id not in seen:
                    seen.add(result.skill.skill_id)
                    results.append(result)
        results.sort(reverse=True)
        return results[:10]


class SemanticSkillSearchEngine:
    """Wiki-backed semantic search with rule-based fallback.

    The embedding layer ranks by task meaning first. The existing deterministic
    scorer remains as a stabilizer for lifecycle, quality, and exact-name hints.
    """

    def __init__(
        self,
        wiki: Any,
        llm_client: Any,
        *,
        embedding_model: str = "text-embedding-3-small",
        cache_path: Optional[Path] = None,
        semantic_candidate_limit: int = 12,
        graph_candidate_limit: int = 40,
    ) -> None:
        self._wiki = wiki
        self._llm = llm_client
        self._embedding_model = embedding_model
        self._cache_path = Path(cache_path) if cache_path else None
        self._cache: Dict[str, List[float]] = self._load_cache()
        self._disabled = _is_demo_llm(llm_client)
        self._semantic_candidate_limit = semantic_candidate_limit
        self._graph_candidate_limit = graph_candidate_limit

    async def search(self, query: SearchQuery) -> List[SearchResult]:
        skills = await self._wiki.list(
            skill_type=query.skill_type,
            state=query.state,
            tags=query.tags,
            domain=query.domain,
            visibility=query.visibility,
            limit=10000,
        )
        skills = [skill for skill in skills if skill_matches_filters(skill, query)]
        if not query.text or self._disabled:
            return rank_search_results(skills, query)

        candidates = _coarse_skill_candidates(skills, query, limit=max(query.max_results, self._semantic_candidate_limit))
        if not candidates:
            candidates = _dedupe_results([score_skill_match(skill, query) for skill in skills])[:max(query.max_results, self._semantic_candidate_limit)]
        skills = [result.skill for result in candidates]

        query_embedding = self._embed_one(f"User task: {query.text}")
        if query_embedding is None:
            return rank_search_results(skills, query)

        semantic_texts = [skill_semantic_text(skill) for skill in skills]
        skill_embeddings = self._embed_many(semantic_texts)
        if skill_embeddings is None:
            return rank_search_results(skills, query)

        results: List[SearchResult] = []
        for skill, skill_embedding in zip(skills, skill_embeddings):
            semantic_score = _cosine_similarity(query_embedding, skill_embedding)
            rule_result = score_skill_match(skill, query)
            combined = max(0.0, min(1.0, semantic_score * 0.74 + rule_result.score * 0.18 + _state_score(skill.state) * 0.08))
            reasons = ["embedding semantic match"]
            if rule_result.match_reasons:
                reasons.extend(rule_result.match_reasons)
            if combined <= 0.10:
                continue
            results.append(SearchResult(skill=skill, score=round(combined, 6), match_reasons=_unique_reasons(reasons)))

        results.sort(reverse=True)
        return _dedupe_results(results)[: query.max_results]

    async def search_text(self, text: str, limit: int = 10) -> List[SearchResult]:
        return await self.search(SearchQuery(text=text, max_results=limit))

    def rank_graph_nodes(self, nodes: Iterable[Any], query_text: str, *, limit: int = 12) -> List[dict[str, Any]]:
        if not query_text or self._disabled:
            return []
        query_embedding = self._embed_one(f"User task graph query: {query_text}")
        if query_embedding is None:
            return []
        node_list = _coarse_graph_nodes(nodes, query_text, limit=max(limit * 3, self._graph_candidate_limit))
        if not node_list:
            return []
        texts = [graph_node_semantic_text(node) for node in node_list]
        node_embeddings = self._embed_many(texts)
        if node_embeddings is None:
            return []
        ranked: List[tuple[float, dict[str, Any]]] = []
        for node, node_embedding in zip(node_list, node_embeddings):
            score = _cosine_similarity(query_embedding, node_embedding)
            if score <= 0.10:
                continue
            ranked.append((score, graph_node_to_public_dict(node, score=score)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in ranked[:limit]]

    def _embed_one(self, text: str) -> Optional[List[float]]:
        key = _embedding_cache_key(self._embedding_model, text)
        if key in self._cache:
            return self._cache[key]
        try:
            vectors = self._llm.embed([text], model=self._embedding_model)
        except Exception as exc:
            logger.warning("Embedding search disabled after provider failure: %s", exc)
            self._disabled = True
            return None
        if not vectors:
            return None
        self._cache[key] = vectors[0]
        return vectors[0]

    def _embed_many(self, texts: List[str]) -> Optional[List[List[float]]]:
        keys = [_embedding_cache_key(self._embedding_model, text) for text in texts]
        missing: List[str] = []
        missing_keys: List[str] = []
        for key, text in zip(keys, texts):
            if key not in self._cache:
                missing.append(text)
                missing_keys.append(key)
        if missing:
            try:
                vectors = self._llm.embed(missing, model=self._embedding_model)
            except Exception as exc:
                logger.warning("Embedding search disabled after provider failure: %s", exc)
                self._disabled = True
                return None
            for key, vector in zip(missing_keys, vectors):
                self._cache[key] = vector
            self._persist_cache()
        return [self._cache[key] for key in keys]

    async def warmup(self, *, include_graph_nodes: Optional[Iterable[Any]] = None) -> None:
        """Pre-compute stable Skill and graph embeddings so execution only embeds queries."""
        if self._disabled:
            return
        skills = await self._wiki.list(visibility=SkillVisibility.USER.value, limit=10000)
        texts = [skill_semantic_text(skill) for skill in skills]
        if include_graph_nodes:
            texts.extend(graph_node_semantic_text(node) for node in _coarse_graph_nodes(include_graph_nodes, "", limit=80))
        if texts:
            self._embed_many(texts)

    def _load_cache(self) -> Dict[str, List[float]]:
        if not self._cache_path or not self._cache_path.exists():
            return {}
        try:
            with self._cache_path.open("r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            logger.warning("Failed to load embedding cache %s: %s", self._cache_path, exc)
            return {}
        if not isinstance(raw, dict):
            return {}
        vectors: Dict[str, List[float]] = {}
        for key, value in raw.items():
            if isinstance(key, str) and isinstance(value, list):
                try:
                    vectors[key] = [float(item) for item in value]
                except (TypeError, ValueError):
                    continue
        return vectors

    def _persist_cache(self) -> None:
        if not self._cache_path:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._cache_path.with_suffix(f"{self._cache_path.suffix}.tmp")
            with tmp_path.open("w", encoding="utf-8") as fh:
                json.dump(self._cache, fh)
            tmp_path.replace(self._cache_path)
        except Exception as exc:
            logger.warning("Failed to persist embedding cache %s: %s", self._cache_path, exc)


def rank_search_results(skills: Iterable[Skill], query: SearchQuery) -> List[SearchResult]:
    """Filter, score, deduplicate, and stably sort Skills for a query."""

    results: List[SearchResult] = []
    for skill in skills:
        if not skill_matches_filters(skill, query):
            continue
        result = score_skill_match(skill, query)
        if result.score <= 0 and query.text:
            continue
        results.append(result)

    return _dedupe_results(results)[: query.max_results]


def skill_semantic_text(skill: Skill) -> str:
    interface = skill.interface
    input_props = interface.input_schema.get("properties", {}) if interface else {}
    output_props = interface.output_schema.get("properties", {}) if interface else {}
    preconditions = interface.preconditions if interface else []
    postconditions = interface.postconditions if interface else []
    tool_calls = skill.implementation.tool_calls if skill.implementation else []
    prompt_excerpt = ""
    if skill.implementation and skill.implementation.prompt_template:
        prompt_excerpt = skill.implementation.prompt_template[:1800]
    provenance_context = skill.provenance.creation_context if skill.provenance else {}
    return "\n".join([
        f"Skill name: {skill.name}",
        f"Display name: {skill.display_name}",
        f"Source format: {getattr(skill, 'source_format', 'skillos')}",
        f"Final immutable: {getattr(skill, 'is_final', False) or getattr(skill, 'immutable', False)}",
        f"Type: {skill.skill_type.value}",
        f"Domain: {skill.domain}",
        f"Description: {skill.description}",
        f"Tags: {', '.join(skill.tags)}",
        f"Original skill metadata: {provenance_context}",
        f"Instruction excerpt: {prompt_excerpt}",
        f"Inputs: {input_props}",
        f"Outputs: {output_props}",
        f"Tools: {', '.join(tool_calls)}",
        f"Preconditions: {'; '.join(preconditions)}",
        f"Postconditions: {'; '.join(postconditions)}",
    ])


def graph_node_semantic_text(node: Any) -> str:
    node_type = getattr(getattr(node, "node_type", None), "value", getattr(node, "node_type", ""))
    return "\n".join([
        f"Graph node type: {node_type}",
        f"Name: {getattr(node, 'name', '')}",
        f"Description: {getattr(node, 'description', '')}",
        f"Labels: {', '.join(getattr(node, 'labels', []) or [])}",
        f"Metadata: {getattr(node, 'metadata', {}) or {}}",
    ])


def graph_node_to_public_dict(node: Any, *, score: float = 0.0) -> dict[str, Any]:
    node_type = getattr(getattr(node, "node_type", None), "value", getattr(node, "node_type", ""))
    metadata = getattr(node, "metadata", {}) or {}
    public = {
        "id": getattr(node, "node_id", ""),
        "name": getattr(node, "name", ""),
        "node_type": node_type,
        "description": getattr(node, "description", ""),
        "labels": getattr(node, "labels", []) or [],
        "metadata": metadata,
    }
    if score:
        public["semantic_score"] = round(score, 4)
    return public


def skill_matches_filters(skill: Skill, query: SearchQuery) -> bool:
    if query.skill_type and skill.skill_type != query.skill_type:
        return False
    if query.domain and skill.domain != query.domain:
        return False
    if query.state and skill.state != query.state:
        return False
    if not query.include_deprecated and skill.state in (SkillState.DEPRECATED, SkillState.ARCHIVED):
        return False
    if query.tags:
        requested_tags = normalize_tags(query.tags)
        if not requested_tags & set(skill.tags):
            return False
    if query.min_success_rate > 0 and skill.metrics.success_rate < query.min_success_rate:
        return False
    if query.visibility and query.visibility != "all" and skill.visibility.value != query.visibility:
        return False
    return True


def score_skill_match(skill: Skill, query: SearchQuery) -> SearchResult:
    """Return a deterministic rule-based relevance score for one Skill."""

    score = 0.0
    reasons: List[str] = []
    query_tokens = set(extract_query_tokens(query.text))
    normalized_query = normalize_text(query.text)
    normalized_name = normalize_text(skill.name)
    normalized_display_name = normalize_text(skill.display_name)

    if query_tokens:
        if normalized_query and normalized_query in {normalized_name, normalized_display_name}:
            score += 0.42
            reasons.append("exact name match")
        else:
            name_tokens = set(extract_query_tokens(f"{skill.name} {skill.display_name}"))
            overlap = query_tokens & name_tokens
            if overlap:
                score += 0.30 * len(overlap) / len(query_tokens)
                reasons.append("name token match")

            description_tokens = set(extract_query_tokens(skill.description))
            desc_overlap = query_tokens & description_tokens
            if desc_overlap:
                score += 0.16 * len(desc_overlap) / len(query_tokens)
                reasons.append("description match")

            tag_overlap = query_tokens & set(skill.tags)
            if tag_overlap:
                score += 0.10 * len(tag_overlap) / len(query_tokens)
                reasons.append("tag match")

            if skill.domain and skill.domain.lower() in query_tokens:
                score += 0.06
                reasons.append("domain match")

    requested_tags = normalize_tags(query.tags)
    if requested_tags:
        matched_tags = requested_tags & set(skill.tags)
        if matched_tags:
            score += 0.12 * len(matched_tags) / len(requested_tags)
            reasons.append("tag match")

    if query.domain and skill.domain == query.domain:
        score += 0.05
        reasons.append("domain match")

    quality_score = _quality_score(skill)
    score += quality_score * 0.16
    if skill.metrics.total_executions:
        reasons.append("success rate boost")

    state_boost = _state_score(skill.state) * 0.09
    score += state_boost
    if state_boost > 0:
        reasons.append("state boost")

    if not query_tokens and not requested_tags and not query.domain:
        score = quality_score * 0.65 + _state_score(skill.state) * 0.35

    return SearchResult(
        skill=skill,
        score=max(0.0, min(round(score, 6), 1.0)),
        match_reasons=_unique_reasons(reasons),
    )


def extract_query_tokens(text: str) -> List[str]:
    tokens = re.split(r"[\s\.,;:!?\-_/\\]+", normalize_text(text))
    return [token for token in tokens if token and token not in STOPWORDS]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", " ").strip().lower())


def normalize_tags(tags: Iterable[str]) -> Set[str]:
    return {tag.strip().lower() for tag in tags if tag and tag.strip()}


def _quality_score(skill: Skill) -> float:
    success = skill.metrics.success_rate if skill.metrics.total_executions else 0.5
    usage = min(skill.metrics.usage_count / 100, 1.0)
    return success * 0.75 + usage * 0.25


def _state_score(state: SkillState) -> float:
    return {
        SkillState.RELEASED: 1.0,
        SkillState.VERIFIED: 0.75,
        SkillState.DEGRADED: 0.45,
        SkillState.SKILL_CANDIDATE: 0.35,
        SkillState.DRAFT: 0.25,
    }.get(state, 0.05)


def _result_sort_key(result: SearchResult) -> tuple:
    skill = result.skill
    return (
        result.score,
        _state_score(skill.state),
        skill.metrics.success_rate,
        skill.metrics.usage_count,
        skill.updated_at,
        skill.name,
        skill.version,
    )


def _dedupe_results(results: Iterable[SearchResult]) -> List[SearchResult]:
    ordered = sorted(results, reverse=True)
    best_by_name: Dict[str, SearchResult] = {}
    for result in ordered:
        existing = best_by_name.get(result.skill.name)
        if existing is None or _result_sort_key(result) > _result_sort_key(existing):
            best_by_name[result.skill.name] = result
    deduped = list(best_by_name.values())
    deduped.sort(reverse=True)
    return deduped


def _cosine_similarity(left: List[float], right: List[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    # Map [-1, 1] to [0, 1] for easier fusion with rule scores.
    return (dot / (left_norm * right_norm) + 1.0) / 2.0


def _embedding_cache_key(model: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    return f"{model}:{digest}"


def _coarse_skill_candidates(skills: Iterable[Skill], query: SearchQuery, *, limit: int) -> List[SearchResult]:
    candidates = [
        result for result in (score_skill_match(skill, query) for skill in skills)
        if result.score > 0
    ]
    if not candidates and query.text:
        tokens = set(extract_query_tokens(query.text))
        for skill in skills:
            text = skill_semantic_text(skill)
            if tokens & set(extract_query_tokens(text)):
                candidates.append(SearchResult(skill=skill, score=_state_score(skill.state) * 0.2, match_reasons=["coarse token match"]))
    return _dedupe_results(candidates)[:limit]


def _coarse_graph_nodes(nodes: Iterable[Any], query_text: str, *, limit: int) -> List[Any]:
    node_list = list(nodes)
    if not query_text:
        return [
            node for node in node_list
            if getattr(getattr(node, "node_type", None), "value", getattr(node, "node_type", "")) != "skill"
        ][:limit]
    tokens = set(extract_query_tokens(query_text))
    if not tokens:
        return node_list[:limit]
    scored: List[tuple[float, Any]] = []
    for node in node_list:
        node_type = getattr(getattr(node, "node_type", None), "value", getattr(node, "node_type", ""))
        if node_type == "skill":
            continue
        node_text = graph_node_semantic_text(node)
        node_tokens = set(extract_query_tokens(node_text))
        overlap = tokens & node_tokens
        if not overlap:
            continue
        scored.append((len(overlap) / max(len(tokens), 1), node))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [node for _, node in scored[:limit]]


def _is_demo_llm(llm_client: Any) -> bool:
    api_key = str(getattr(getattr(llm_client, "_cfg", None), "api_key", ""))
    return api_key.startswith("local-") or api_key.startswith("demo-")


def _unique_reasons(reasons: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    unique: List[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique


STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "by", "from",
    "that", "this", "it", "its", "and", "or", "but", "not",
    "can", "will", "should", "would", "could", "may", "might",
    "how", "what", "when", "where", "which", "who",
}
