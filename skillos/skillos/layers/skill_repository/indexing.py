"""Skill 搜索引擎 — 全文搜索 + 语义相似度检索。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...models.skill_model import Skill, SkillState, SkillType
from ...storage.postgres_db import PostgresConnection, SkillRepository
from ...utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """搜索结果条目。"""
    skill: Skill
    score: float                    # 综合相关性分数 [0, 1]
    match_reasons: List[str] = field(default_factory=list)

    def __lt__(self, other: "SearchResult") -> bool:
        return self.score < other.score


@dataclass
class SearchQuery:
    """结构化搜索查询。"""
    text: str = ""                              # 自然语言查询
    tags: List[str] = field(default_factory=list)
    skill_type: Optional[SkillType] = None
    domain: Optional[str] = None
    state: Optional[SkillState] = None
    min_success_rate: float = 0.0
    max_results: int = 20
    include_deprecated: bool = False


class SkillSearchEngine:
    """Skill 检索引擎。

    当前实现：基于 PostgreSQL 的关键词匹配 + 多维度评分。
    未来可替换为向量数据库（pgvector / Milvus）实现语义检索。
    """

    def __init__(self, pg_conn: PostgresConnection) -> None:
        self._repo = SkillRepository(pg_conn)

    async def search(self, query: SearchQuery) -> List[SearchResult]:
        """执行搜索，返回按相关性排序的结果列表。"""
        # 1. 构建过滤条件
        filters: Dict[str, Any] = {}
        if query.skill_type:
            filters["skill_type"] = query.skill_type.value
        if query.domain:
            filters["domain"] = query.domain
        if query.state:
            filters["state"] = query.state.value
        elif not query.include_deprecated:
            # 默认排除 deprecated 和 archived
            pass  # 在后处理中过滤

        # 2. 候选集获取
        candidates: List[Skill] = []

        if query.text:
            # 名称模糊匹配
            name_matches = await self._repo.list(
                filters={**filters, "name_like": query.text},
                limit=query.max_results * 3,
            )
            candidates.extend(name_matches)

        if query.tags:
            tag_matches = await self._repo.search_by_tags(
                query.tags, limit=query.max_results * 2
            )
            # 去重
            existing_ids = {s.skill_id for s in candidates}
            candidates.extend(s for s in tag_matches if s.skill_id not in existing_ids)

        if not candidates:
            # 无特定条件时返回全量（按使用量排序）
            candidates = await self._repo.list(filters=filters, limit=query.max_results * 2)

        # 3. 后处理过滤
        if not query.include_deprecated:
            candidates = [
                s for s in candidates
                if s.state not in (SkillState.DEPRECATED, SkillState.ARCHIVED)
            ]
        if query.min_success_rate > 0:
            candidates = [
                s for s in candidates
                if s.metrics.success_rate >= query.min_success_rate
            ]

        # 4. 评分排序
        results = [self._score(s, query) for s in candidates]
        results.sort(reverse=True)

        # 5. 去重（同名取最高版本）
        seen_names: Dict[str, SearchResult] = {}
        for r in results:
            name = r.skill.name
            if name not in seen_names or r.score > seen_names[name].score:
                seen_names[name] = r

        return list(seen_names.values())[: query.max_results]

    async def search_text(self, text: str, limit: int = 10) -> List[SearchResult]:
        """快捷方法：纯文本搜索。"""
        return await self.search(SearchQuery(text=text, max_results=limit))

    async def find_by_capability(
        self,
        capability_description: str,
        domain: Optional[str] = None,
    ) -> List[SearchResult]:
        """按能力描述查找 Skill（关键词提取 + 多字段匹配）。"""
        keywords = self._extract_keywords(capability_description)
        results: List[SearchResult] = []
        seen: set = set()

        for kw in keywords[:5]:  # 取前 5 个关键词
            matches = await self.search(
                SearchQuery(text=kw, domain=domain, max_results=10)
            )
            for r in matches:
                if r.skill.skill_id not in seen:
                    seen.add(r.skill.skill_id)
                    results.append(r)

        results.sort(reverse=True)
        return results[:10]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _score(self, skill: Skill, query: SearchQuery) -> SearchResult:
        """多维度评分：相关性 + 质量 + 新鲜度。"""
        score = 0.0
        reasons: List[str] = []

        # --- 文本相关性 (40%) ---
        if query.text:
            text_score = self._text_relevance(skill, query.text)
            score += text_score * 0.4
            if text_score > 0:
                reasons.append(f"文本匹配 {text_score:.2f}")

        # --- 标签匹配 (20%) ---
        if query.tags:
            tag_hits = sum(1 for t in query.tags if t in skill.tags)
            tag_score = tag_hits / len(query.tags)
            score += tag_score * 0.2
            if tag_hits:
                reasons.append(f"标签匹配 {tag_hits}/{len(query.tags)}")

        # --- 质量分 (25%): 成功率 + 使用量 ---
        quality = skill.metrics.success_rate * 0.6
        usage_norm = min(skill.metrics.usage_count / 1000, 1.0) * 0.4
        quality_score = quality + usage_norm
        score += quality_score * 0.25
        if skill.metrics.usage_count > 0:
            reasons.append(f"成功率 {skill.metrics.success_rate:.0%}")

        # --- 状态加成 (15%) ---
        state_bonus = {
            SkillState.RELEASED: 1.0,
            SkillState.VERIFIED: 0.8,
            SkillState.DEGRADED: 0.5,
            SkillState.DRAFT: 0.3,
        }.get(skill.state, 0.1)
        score += state_bonus * 0.15

        # 无文本和标签查询时，直接用质量分
        if not query.text and not query.tags:
            score = quality_score * 0.7 + state_bonus * 0.3

        return SearchResult(skill=skill, score=min(score, 1.0), match_reasons=reasons)

    def _text_relevance(self, skill: Skill, query_text: str) -> float:
        """计算文本相关性分数。"""
        query_lower = query_text.lower()
        query_tokens = set(re.split(r"[\s_\-]+", query_lower))

        # 名称完全匹配
        if skill.name == query_lower.replace(" ", "_"):
            return 1.0

        # 名称包含查询
        if query_lower.replace(" ", "_") in skill.name:
            return 0.9

        # 名称 token 匹配
        name_tokens = set(re.split(r"[\s_\-]+", skill.name))
        token_overlap = len(query_tokens & name_tokens) / max(len(query_tokens), 1)
        if token_overlap > 0:
            return 0.5 + token_overlap * 0.4

        # 描述匹配
        desc_lower = skill.description.lower()
        if query_lower in desc_lower:
            return 0.4

        # 标签匹配
        if any(query_lower in tag for tag in skill.tags):
            return 0.3

        return 0.0

    def _extract_keywords(self, text: str) -> List[str]:
        """从自然语言描述中提取关键词（简单规则，无 NLP 依赖）。"""
        # 去除停用词
        stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "to", "of", "in", "on", "at", "for", "with", "by", "from",
            "that", "this", "it", "its", "and", "or", "but", "not",
            "can", "will", "should", "would", "could", "may", "might",
            "how", "what", "when", "where", "which", "who",
        }
        tokens = re.split(r"[\s\.,;:!?\-_/\\]+", text.lower())
        keywords = [t for t in tokens if t and t not in stopwords and len(t) > 2]
        # 按长度降序（更长的词通常更具体）
        keywords.sort(key=len, reverse=True)
        return keywords
