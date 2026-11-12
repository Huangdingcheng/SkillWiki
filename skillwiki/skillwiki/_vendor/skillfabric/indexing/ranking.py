"""Rank-only fusion for independent retrieval channels."""

from __future__ import annotations

from dataclasses import dataclass

RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusedRank:
    skill_id: str
    score: float
    ranks: dict[str, int]


def reciprocal_rank_fusion(
    channels: dict[str, list[str]],
    *,
    k: int = RRF_K,
) -> list[FusedRank]:
    """Fuse independent rankings without comparing their raw scores."""

    if k < 0:
        raise ValueError("RRF k must be non-negative")
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for channel, skill_ids in sorted(channels.items()):
        seen: set[str] = set()
        for rank, skill_id in enumerate(skill_ids, start=1):
            if skill_id in seen:
                continue
            seen.add(skill_id)
            scores[skill_id] = scores.get(skill_id, 0.0) + (1.0 / (k + rank))
            ranks.setdefault(skill_id, {})[channel] = rank
    return sorted(
        (
            FusedRank(skill_id=skill_id, score=score, ranks=ranks[skill_id])
            for skill_id, score in scores.items()
        ),
        key=lambda row: (-row.score, row.skill_id),
    )
