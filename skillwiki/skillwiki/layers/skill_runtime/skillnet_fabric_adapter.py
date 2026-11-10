"""Thin SkillNet-Fabric retrieval adapter for the SkillWiki demo.

Fabric is loaded from the pinned source snapshot in this repository. SkillWiki
remains authoritative for lifecycle state, versions, and execution.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FABRIC_COMMIT = "e0bc52856e8244ef6eecc0fe8c1b5f481b4190a3"
FABRIC_MODEL_ID = "skillwiki/local-hash-embedding-v1"
_DIMENSION = 128
_WORKSPACES: dict[str, Path] = {}
_BUDGET_LOCKS: dict[str, threading.Lock] = {}
_BUDGET_LOCKS_GUARD = threading.Lock()

@dataclass(frozen=True)
class FabricRetrievalResult:
    candidates: tuple[dict[str, Any], ...]
    alternatives: tuple[dict[str, Any], ...]
    source_commit: str = FABRIC_COMMIT
    embedding_model: str = FABRIC_MODEL_ID
    embedding_mode: str = "deterministic_local_fixture"


def _paperqa_env_path() -> Path:
    configured = os.environ.get("SKILLWIKI_PAPERQA_ENV_FILE")
    if not configured:
        raise RuntimeError("Set SKILLWIKI_PAPERQA_ENV_FILE for remote embeddings")
    return Path(configured).expanduser()


def _remote_embedding_provider() -> tuple[Any, str]:
    """Load explicitly configured PaperQA-compatible embedding settings without copying secrets.

    Remote use is deliberately fail-closed: an explicit mode and an explicit
    budget ledger are required before any provider call can happen. This lets
    the normal demo remain zero-cost while keeping the real configured path
    available to a deliberate smoke run.
    """
    _fabric_source()
    from skillfabric.indexing.embeddings import ApiEmbeddingProvider
    from skillfabric.runtime.llm import read_env_file

    env_path = _paperqa_env_path()
    values = read_env_file(env_path)
    model = (
        values.get("PAPERQA_EMBEDDING_MODEL")
        or values.get("EMBEDDING_MODEL")
        or os.environ.get("PAPERQA_EMBEDDING_MODEL")
    )
    api_key = (
        values.get("PAPERQA_OPENAI_API_KEY")
        or values.get("PAPERQA_API_KEY")
        or values.get("OPENAI_API_KEY")
        or values.get("EMBEDDING_API_KEY")
        or os.environ.get("PAPERQA_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    api_base = (
        values.get("PAPERQA_OPENAI_API_BASE")
        or values.get("PAPERQA_OPENAI_BASE_URL")
        or values.get("OPENAI_API_BASE")
        or values.get("OPENAI_BASE_URL")
        or values.get("EMBEDDING_BASE_URL")
        or os.environ.get("PAPERQA_OPENAI_API_BASE")
        or os.environ.get("PAPERQA_OPENAI_BASE_URL")
        or os.environ.get("OPENAI_API_BASE")
        or os.environ.get("OPENAI_BASE_URL")
    )
    if not env_path.is_file() or not model or not api_key or not api_base:
        raise RuntimeError(
            "Remote embedding configuration is incomplete; "
            "endpoint and credentials were not loaded"
        )
    dimension_raw = (
        values.get("PAPERQA_EMBEDDING_DIMENSION")
        or values.get("EMBEDDING_DIMENSION")
        or os.environ.get("PAPERQA_EMBEDDING_DIMENSION")
        # Bailian text-embedding-v4 returns 1024 dimensions unless the
        # endpoint explicitly overrides that setting.
        or "1024"
    )
    try:
        dimension = int(dimension_raw)
    except ValueError as exc:
        raise RuntimeError("PaperQA embedding dimension is invalid") from exc
    provider = ApiEmbeddingProvider(
        model_id=model,
        dimension=dimension,
        api_key=api_key,
        api_base=api_base,
        timeout=float(values.get("EMBEDDING_TIMEOUT", "120")),
        batch_size=1,
        concurrency=1,
        max_text_chars=int(values.get("EMBEDDING_TEXT_CHARS", "4000")),
        # Fabric's provider requires at least one; the outer gate still keeps
        # calls bounded and disables automatic application-level retries.
        max_retries=1,
    )
    # Pinned Fabric validates >=1 on construction, but forwards this value
    # directly to LiteLLM. The Demo transport must make exactly one attempt.
    object.__setattr__(provider, "max_retries", 0)
    return provider, "configured_remote"


def _embedding_provider() -> tuple[Any, str]:
    mode = os.environ.get("SKILLWIKI_FABRIC_EMBEDDING_MODE", "fixture").strip().lower()
    if mode in {"fixture", "local", "deterministic"}:
        return _LocalHashEmbedding(), "deterministic_local_fixture"
    if mode != "remote":
        raise RuntimeError("SKILLWIKI_FABRIC_EMBEDDING_MODE must be fixture or remote")
    if os.environ.get("SKILLWIKI_FABRIC_EMBEDDING_ALLOW_REMOTE") != "1":
        raise RuntimeError(
            "remote embedding is disabled; set SKILLWIKI_FABRIC_EMBEDDING_ALLOW_REMOTE=1 "
            "for a budgeted smoke run"
        )
    ledger = os.environ.get("SKILLWIKI_FABRIC_EMBEDDING_LEDGER_PATH")
    if not ledger or not os.environ.get("SKILLWIKI_FABRIC_EMBEDDING_BUDGET_LIMIT_CNY"):
        raise RuntimeError(
            "remote embedding requires an explicit budget limit and ledger path"
        )
    provider, resolved_mode = _remote_embedding_provider()
    cumulative_path = os.environ.get("SKILLWIKI_CUMULATIVE_BUDGET_LEDGER_PATH")
    if cumulative_path:
        from ...utils.cumulative_budget import CumulativeBudgetGuard
        total = os.environ.get("SKILLWIKI_CUMULATIVE_BUDGET_TOTAL_LIMIT_CNY", "10")
        return _BudgetedEmbedding(
            provider,
            Path(ledger),
            cumulative_guard=CumulativeBudgetGuard(cumulative_path, total_limit_cny=total),
        ), resolved_mode
    return _BudgetedEmbedding(provider, Path(ledger)), resolved_mode


class _BudgetedEmbedding:
    def __init__(self, provider: Any, ledger: Path, cumulative_guard: Any = None) -> None:
        self._provider = provider
        self._ledger = ledger
        self._cumulative_guard = cumulative_guard
        self.model_id = provider.model_id
        self.dimension = provider.dimension

    def embed(self, text: str) -> list[float]:
        if self._cumulative_guard is not None:
            estimate = max(0.000001, len(text.encode("utf-8")) / 1_000_000 * 2.0)
            try:
                request_id = self._cumulative_guard.reserve(
                    "embedding", estimate,
                    metadata={"model": self.model_id, "text_bytes": len(text.encode("utf-8"))},
                )
            except Exception as exc:
                raise RuntimeError(f"cumulative embedding budget rejected request: {exc}") from exc
            try:
                result = list(map(float, self._provider.embed(text)))
            except Exception:
                self._cumulative_guard.settle(request_id)
                raise
            # Provider usage is not exposed by this interface; retain the
            # conservative reservation instead of treating it as zero cost.
            self._cumulative_guard.settle(request_id)
            return result
        self._reserve(text)
        return list(map(float, self._provider.embed(text)))

    def _reserve(self, text: str) -> None:
        try:
            limit = float(os.environ["SKILLWIKI_FABRIC_EMBEDDING_BUDGET_LIMIT_CNY"])
            rate = 2.0
        except (KeyError, ValueError) as exc:
            raise RuntimeError("embedding budget configuration is invalid") from exc
        if limit <= 0 or rate < 0:
            raise RuntimeError("embedding budget configuration is invalid")
        # One token per UTF-8 byte is intentionally conservative, especially
        # for CJK text. The default rate is four times the verified standard
        # China text-embedding-v4 price.
        estimate = max(0.000001, len(text.encode("utf-8")) / 1_000_000 * rate)
        self._ledger.parent.mkdir(parents=True, exist_ok=True)
        key = os.path.normcase(str(self._ledger.resolve()))
        with _BUDGET_LOCKS_GUARD:
            thread_lock = _BUDGET_LOCKS.setdefault(key, threading.Lock())
        with thread_lock:
            lock_path = Path(str(self._ledger) + ".lock")
            with lock_path.open("a+b") as lock_file:
                if os.name == "nt":
                    import msvcrt
                    lock_file.seek(0)
                    if lock_file.tell() == 0 and lock_file.seek(0, os.SEEK_END) == 0:
                        lock_file.write(b"0")
                        lock_file.flush()
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    used = 0.0
                    if self._ledger.exists():
                        for line in self._ledger.read_text(encoding="utf-8").splitlines():
                            try:
                                row = json.loads(line)
                                used += float(row["reserved_cny"])
                            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
                                raise RuntimeError("embedding budget ledger is invalid") from exc
                    if used + estimate > limit + 1e-12:
                        raise RuntimeError(
                            f"embedding budget exceeded: reserved={used:.6f} limit={limit:.6f}"
                        )
                    with self._ledger.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps({
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "model": self.model_id,
                            "characters": len(text),
                            "reserved_cny": estimate,
                            "status": "reserved",
                        }, ensure_ascii=False) + "\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                finally:
                    if os.name == "nt":
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)


@dataclass(frozen=True)
class _EmbeddingRecord:
    key: str
    skill_id: str
    kind: str
    field_name: str
    text_hash: str
    vector: list[float]


class _LocalHashEmbedding:
    """Deterministic fixture channel; deliberately makes no network calls."""

    model_id = FABRIC_MODEL_ID
    dimension = _DIMENSION

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        tokens = re.findall(r"[\w-]+", text.casefold(), flags=re.UNICODE)
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        if not any(vector):
            vector[0] = 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector]


def _fabric_source() -> Path:
    root = Path(__file__).resolve().parents[4]
    source = root / "artifacts" / "20260927-content-vertical-slice" / "SkillNet-REF-02" / "source" / "src"
    if not (source / "skillfabric" / "router" / "retrieval.py").is_file():
        raise RuntimeError("pinned SkillNet-Fabric source is missing")
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    return source


def _skill_text(skill: Any) -> str:
    implementation = getattr(skill, "implementation", None)
    fields = [
        getattr(skill, "name", ""),
        getattr(skill, "description", ""),
        " ".join(getattr(skill, "tags", []) or []),
        getattr(implementation, "prompt_template", "") if implementation else "",
        getattr(implementation, "code", "") if implementation else "",
    ]
    return "\n".join(str(value) for value in fields if value)


def _fabric_edge_type(edge_type: Any) -> str | None:
    value = getattr(edge_type, "value", edge_type)
    return {
        "depends_on": "depend_on",
        "composes_with": "compose_with",
        "similar_to": "similar_to",
    }.get(str(value))


def retrieve_with_fabric(
    query: str,
    skills: Iterable[Any],
    graph_edges: Iterable[Any] = (),
    *,
    limit: int = 10,
    max_depth: int = 2,
) -> FabricRetrievalResult:
    """Run pinned Fabric BM25+embedding retrieval and semantic expansion."""

    if not query.strip():
        raise ValueError("retrieval query must not be empty")
    if limit <= 0:
        raise ValueError("candidate limit must be positive")
    skills = list(skills)
    if not skills:
        return FabricRetrievalResult(candidates=(), alternatives=())

    _fabric_source()
    from skillfabric.graph.models import Edge
    from skillfabric.indexing.bm25 import build_bm25_index
    from skillfabric.indexing.embeddings import write_binary_embedding_store
    from skillfabric.registry.models import SkillNode
    from skillfabric.router.expansion import expand_semantic_candidates
    from skillfabric.router.retrieval import retrieve_seed_candidates
    from skillfabric.storage import Workspace

    nodes: dict[str, SkillNode] = {}
    vectors, embedding_mode = _embedding_provider()
    records: list[_EmbeddingRecord] = []
    for skill in skills:
        skill_id = str(skill.skill_id)
        text = _skill_text(skill)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        nodes[skill_id] = SkillNode(
            id=skill_id,
            type="skill",
            name=str(skill.name),
            description=str(skill.description or ""),
            content_hash=digest,
            raw_text=text,
        )
        records.append(_EmbeddingRecord(
            key=f"{skill_id}:skill:canonical",
            skill_id=skill_id,
            kind="skill",
            field_name="canonical",
            text_hash=digest,
            vector=vectors.embed(text),
        ))

    corpus_digest = hashlib.sha256((
        embedding_mode + ":" + str(getattr(vectors, "model_id", "")) + ":" + "\n".join(
            f"{skill_id}:{nodes[skill_id].content_hash}" for skill_id in sorted(nodes)
        )
    ).encode("utf-8")).hexdigest()[:20]
    workspace_root = _WORKSPACES.get(corpus_digest)
    if workspace_root is None:
        # Fabric's embedding loader memory-maps the matrix and caches it for
        # the process lifetime. Keep this disposable derived index in the OS
        # temp area instead of deleting a still-mapped file on Windows.
        workspace_root = Path(tempfile.mkdtemp(prefix=f"skillwiki-fabric-{corpus_digest}-"))
        _WORKSPACES[corpus_digest] = workspace_root
    workspace = Workspace(workspace_root)
    workspace.ensure()
    if not (workspace.graph_dir / "bm25.sqlite").exists():
        build_bm25_index(list(nodes.values()), workspace.graph_dir / "bm25.sqlite")
    if not (workspace.graph_dir / "embeddings.json").exists():
        write_binary_embedding_store(
            workspace.graph_dir / "embeddings.json",
            model_id=str(vectors.model_id),
            records=records,
        )
    seeds = retrieve_seed_candidates(
        workspace,
        query,
        nodes,
        limit=min(max(1, limit // 2), len(nodes)),
        env_file=None,
        embedding_provider=vectors,
    )

    fabric_edges: list[Edge] = []
    for edge in graph_edges:
        edge_type = _fabric_edge_type(getattr(edge, "edge_type", ""))
        source = str(getattr(edge, "source_id", ""))
        target = str(getattr(edge, "target_id", ""))
        if edge_type is None or source not in nodes or target not in nodes:
            continue
        if edge_type == "similar_to" and source > target:
            source, target = target, source
        fabric_edges.append(Edge(
            source=source,
            target=target,
            type=edge_type,
            confidence=float(getattr(edge, "confidence", 1.0)),
            reason=str(getattr(edge, "description", "") or edge_type),
        ))

    expanded = expand_semantic_candidates(
        seeds,
        fabric_edges,
        nodes,
        max_depth=max_depth,
        limit=max(limit, len(seeds)),
    )
    payloads = []
    for candidate in expanded.candidates:
        paths = []
        for path in candidate.introduced_by:
            paths.append({
                "seed_skill_id": path.seed_skill,
                "steps": [
                    {
                        "source": step.source,
                        "target": step.target,
                        "edge_type": step.edge_type,
                        "confidence": step.confidence,
                        "reason": step.reason,
                    }
                    for step in path.steps
                ],
            })
        payloads.append({
            "skill_id": candidate.skill_id,
            "name": candidate.name,
            "content_hash": nodes[candidate.skill_id].content_hash,
            "score": float(candidate.score),
            "is_seed": bool(candidate.is_seed),
            "retrieval_ranks": dict(candidate.retrieval_ranks),
            "graph_depth": int(candidate.graph_depth),
            "paths": paths,
        })
    alternatives = tuple({
        "skill_id": item.skill_id,
        "name": item.name,
        "alternative_to": item.alternative_to,
        "confidence": item.confidence,
        "reason": item.reason,
    } for item in expanded.alternatives)
    return FabricRetrievalResult(
        candidates=tuple(payloads),
        alternatives=alternatives,
        embedding_model=str(vectors.model_id),
        embedding_mode=embedding_mode,
    )
