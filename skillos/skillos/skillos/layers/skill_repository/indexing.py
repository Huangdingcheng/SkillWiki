"""Rule-based Skill search and ranking utilities."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

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
