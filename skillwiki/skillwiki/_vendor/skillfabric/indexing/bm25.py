"""SQLite FTS5 indexing with explicit raw BM25 ranks."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from skillwiki._vendor.skillfabric.indexing.canonical import canonical_skill_text, contract_skill_text
from skillwiki._vendor.skillfabric.registry.models import SkillNode

if TYPE_CHECKING:
    from skillwiki._vendor.skillfabric.graph.contracts.models import SkillContract


@dataclass(frozen=True, slots=True)
class BM25Hit:
    """One FTS result with rank order and unmodified SQLite BM25 score."""

    skill_id: str
    rank: int
    raw_score: float


def build_bm25_index(
    skills: list[SkillNode],
    db_path: str | Path,
    *,
    contracts: dict[str, SkillContract] | None = None,
) -> None:
    """Build the canonical contract-aware FTS5 index."""

    if contracts is not None and set(contracts) != {skill.id for skill in skills}:
        raise ValueError("BM25 contracts must exactly match indexed skills")
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE IF EXISTS skills_fts")
        connection.execute(
            "CREATE VIRTUAL TABLE skills_fts USING "
            "fts5(skill_id UNINDEXED, name, description, body, tokenize='unicode61')"
        )
        connection.executemany(
            "INSERT INTO skills_fts(skill_id, name, description, body) VALUES (?, ?, ?, ?)",
            [
                (
                    skill.id,
                    skill.name,
                    skill.description,
                    contract_skill_text(skill, contracts[skill.id])
                    if contracts is not None
                    else canonical_skill_text(skill),
                )
                for skill in skills
            ],
        )
        connection.commit()


def search_bm25(
    db_path: str | Path,
    query: str,
    *,
    limit: int = 10,
) -> list[BM25Hit]:
    """Return ordered FTS matches without converting rank into confidence."""

    if not query.strip() or limit <= 0:
        return []
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"BM25 index not found: {path}; rebuild the workspace")
    safe_query = _fts_query(query)
    if not safe_query:
        return []
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """
            SELECT skill_id, bm25(skills_fts) AS score
            FROM skills_fts
            WHERE skills_fts MATCH ?
            ORDER BY score, skill_id
            LIMIT ?
            """,
            (safe_query, int(limit)),
        ).fetchall()
    return [
        BM25Hit(skill_id=str(skill_id), rank=rank, raw_score=float(score))
        for rank, (skill_id, score) in enumerate(rows, start=1)
    ]


def _fts_query(query: str) -> str:
    """Escape query tokens for a high-recall OR expression."""

    tokens = list(dict.fromkeys(re.findall(r"[^\W_]+", query.lower(), flags=re.UNICODE)))
    return " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:64])
