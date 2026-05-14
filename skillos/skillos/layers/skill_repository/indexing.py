"""Rule-based Skill search and ranking utilities."""

from __future__ import annotations

import re
import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Protocol, Set

from ...models.skill_model import Skill, SkillState, SkillType
from ...storage.postgres_db import PostgresConnection, SkillRepository
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """Search result entry."""

    skill: Skill
    score: float
    match_reasons: List[str] = field(default_factory=list)
    score_components: Dict[str, float] = field(default_factory=dict)

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
    mode: str = "lexical"


class EmbeddingProvider(Protocol):
    """Minimal pluggable embedding interface for local hybrid search."""

    name: str

    def embed(self, text: str) -> List[float]:
        """Return a deterministic normalized vector for text."""


@dataclass(frozen=True)
class LocalHashEmbeddingProvider:
    """Dependency-free local embedding provider.

    This is intentionally small and deterministic. It combines token features,
    lightweight domain synonym expansion, and character n-grams into a hashed
    normalized vector so P1 hybrid search can run offline.
    """

    dimensions: int = 128
    name: str = "local_hash_embedding"

    def embed(self, text: str) -> List[float]:
        vector = [0.0] * self.dimensions
        for feature, weight in _semantic_features(text):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest, "big") % self.dimensions
            vector[index] += weight
        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude <= 0:
            return vector
        return [round(value / magnitude, 8) for value in vector]


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


def rank_search_results(
    skills: Iterable[Skill],
    query: SearchQuery,
    embedding_provider: Optional[EmbeddingProvider] = None,
) -> List[SearchResult]:
    """Filter, score, deduplicate, and stably sort Skills for a query."""

    results: List[SearchResult] = []
    mode = normalize_search_mode(query.mode)
    for skill in skills:
        if not skill_matches_filters(skill, query):
            continue
        if mode == "hybrid":
            result = score_skill_hybrid(skill, query, embedding_provider=embedding_provider)
            has_query_match = (
                result.score_components.get("lexical", 0.0) > 0
                or result.score_components.get("semantic", 0.0) > 0.01
            )
        else:
            result = score_skill_match(skill, query)
            has_query_match = result.score > 0
        if query.text and not has_query_match:
            continue
        results.append(result)

    results.sort(reverse=True)
    best_by_name: Dict[str, SearchResult] = {}
    for result in results:
        existing = best_by_name.get(result.skill.name)
        if existing is None or _result_sort_key(result) > _result_sort_key(existing):
            best_by_name[result.skill.name] = result
    deduped = list(best_by_name.values())
    deduped.sort(reverse=True)
    return deduped[: query.max_results]


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
        score_components={
            "lexical": _lexical_signal_score(skill, query)[0],
            "semantic": 0.0,
            "health": _health_score(skill),
        },
    )


def score_skill_hybrid(
    skill: Skill,
    query: SearchQuery,
    *,
    embedding_provider: Optional[EmbeddingProvider] = None,
) -> SearchResult:
    """Return a local hybrid lexical/semantic/health relevance score."""

    provider = embedding_provider or LocalHashEmbeddingProvider()
    lexical_score, lexical_reasons = _lexical_signal_score(skill, query)
    semantic_score = _semantic_similarity(skill, query, provider)
    health_score = _health_score(skill)
    score = (0.5 * lexical_score) + (0.4 * semantic_score) + (0.1 * health_score)
    reasons: List[str] = []
    if lexical_score > 0:
        reasons.extend(lexical_reasons or ["lexical match"])
    if semantic_score > 0.01:
        reasons.append("semantic match")
    if health_score > 0.5:
        reasons.append("health boost")
    return SearchResult(
        skill=skill,
        score=max(0.0, min(round(score, 6), 1.0)),
        match_reasons=_unique_reasons(reasons),
        score_components={
            "lexical": lexical_score,
            "semantic": semantic_score,
            "health": health_score,
        },
    )


def cosine_similarity(left: List[float], right: List[float]) -> float:
    """Return cosine similarity for normalized or raw dense vectors."""

    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return max(0.0, min(round(dot / (left_norm * right_norm), 6), 1.0))


def normalize_search_mode(mode: str) -> str:
    normalized = (mode or "lexical").strip().lower()
    if normalized in {"lexical", "rule", "rules", "baseline"}:
        return "lexical"
    if normalized in {"hybrid", "semantic"}:
        return "hybrid"
    raise ValueError(f"Unsupported search mode: {mode!r}")


def extract_query_tokens(text: str) -> List[str]:
    tokens = re.split(r"[\s\.,;:!?\-_/\\]+", normalize_text(text))
    return [token for token in tokens if token and token not in STOPWORDS]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("_", " ").strip().lower())


def normalize_tags(tags: Iterable[str]) -> Set[str]:
    return {tag.strip().lower() for tag in tags if tag and tag.strip()}


def _lexical_signal_score(skill: Skill, query: SearchQuery) -> tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []
    query_tokens = set(extract_query_tokens(query.text))
    normalized_query = normalize_text(query.text)
    normalized_name = normalize_text(skill.name)
    normalized_display_name = normalize_text(skill.display_name)

    if query_tokens:
        if normalized_query and normalized_query in {normalized_name, normalized_display_name}:
            score += 1.0
            reasons.append("exact name match")
        else:
            name_tokens = set(extract_query_tokens(f"{skill.name} {skill.display_name}"))
            name_overlap = query_tokens & name_tokens
            if name_overlap:
                score += 0.45 * len(name_overlap) / len(query_tokens)
                reasons.append("name token match")

            description_tokens = set(extract_query_tokens(skill.description))
            desc_overlap = query_tokens & description_tokens
            if desc_overlap:
                score += 0.24 * len(desc_overlap) / len(query_tokens)
                reasons.append("description match")

            tag_overlap = query_tokens & set(skill.tags)
            if tag_overlap:
                score += 0.16 * len(tag_overlap) / len(query_tokens)
                reasons.append("tag match")

            if skill.domain and skill.domain.lower() in query_tokens:
                score += 0.08
                reasons.append("domain match")

    requested_tags = normalize_tags(query.tags)
    if requested_tags:
        matched_tags = requested_tags & set(skill.tags)
        if matched_tags:
            score += 0.16 * len(matched_tags) / len(requested_tags)
            reasons.append("tag match")

    if query.domain and skill.domain == query.domain:
        score += 0.08
        reasons.append("domain match")

    return max(0.0, min(round(score, 6), 1.0)), _unique_reasons(reasons)


def _semantic_similarity(
    skill: Skill,
    query: SearchQuery,
    embedding_provider: EmbeddingProvider,
) -> float:
    query_text = _query_embedding_text(query)
    if not query_text.strip():
        return 0.0
    return cosine_similarity(
        embedding_provider.embed(query_text),
        embedding_provider.embed(_skill_embedding_text(skill)),
    )


def _query_embedding_text(query: SearchQuery) -> str:
    parts = [query.text, " ".join(query.tags), query.domain or ""]
    return " ".join(part for part in parts if part)


def _skill_embedding_text(skill: Skill) -> str:
    return " ".join([
        skill.name,
        skill.display_name,
        skill.description,
        " ".join(skill.tags),
        skill.domain,
        skill.skill_type.value,
    ])


def _semantic_features(text: str) -> List[tuple[str, float]]:
    tokens = extract_query_tokens(text)
    features: List[tuple[str, float]] = []
    for token in tokens:
        features.append((f"tok:{token}", 1.0))
        for synonym in SEMANTIC_SYNONYMS.get(token, ()):
            features.append((f"tok:{synonym}", 0.85))
        if len(token) >= 4:
            for index in range(len(token) - 2):
                features.append((f"tri:{token[index:index + 3]}", 0.15))
    for left, right in zip(tokens, tokens[1:]):
        features.append((f"bigram:{left}_{right}", 0.5))
    return features


def _health_score(skill: Skill) -> float:
    if skill.metrics.total_executions:
        quality = (
            skill.metrics.success_rate * 0.8
            + min(skill.metrics.usage_count / 100, 1.0) * 0.2
        )
    else:
        quality = 0.5
    score = quality * 0.8 + _state_score(skill.state) * 0.2
    return max(0.0, min(round(score, 6), 1.0))


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


def _unique_reasons(reasons: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    unique: List[str] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique


SEMANTIC_SYNONYMS = {
    "press": ("click", "tap", "select"),
    "tap": ("click", "press", "select"),
    "choose": ("select", "click"),
    "target": ("element", "selector"),
    "field": ("input", "form"),
    "fields": ("input", "form"),
    "enter": ("type", "input"),
    "write": ("type", "text"),
    "send": ("submit", "post"),
    "check": ("verify", "validate"),
    "verification": ("validation", "verify"),
    "validate": ("verify", "check"),
    "repair": ("fix", "maintenance"),
    "fix": ("repair", "maintenance"),
    "failure": ("error", "repair"),
    "source": ("provenance", "origin"),
    "lineage": ("provenance", "source"),
    "history": ("version", "snapshot"),
    "diff": ("compare", "snapshot", "change"),
    "evaluation": ("benchmark", "eval"),
    "eval": ("evaluation", "benchmark"),
    "rollout": ("trajectory", "experience"),
    "trace": ("trajectory", "provenance"),
    "api": ("openapi", "endpoint", "tool"),
    "endpoint": ("api", "openapi"),
    "schema": ("contract", "validation"),
}


STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "by", "from",
    "that", "this", "it", "its", "and", "or", "but", "not",
    "can", "will", "should", "would", "could", "may", "might",
    "how", "what", "when", "where", "which", "who",
}
