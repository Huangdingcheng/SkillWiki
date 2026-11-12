"""Embedding providers and query-vector loading."""

from __future__ import annotations

import json
import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

import numpy as np

from skillwiki._vendor.skillfabric.runtime.llm import DEFAULT_API_BASE, read_env_file

DEFAULT_EMBEDDING_MODEL_ID = "openai/text-embedding-3-small"
DEFAULT_EMBEDDING_DIMENSION = 1536
DEFAULT_EMBEDDING_BATCH_SIZE = 64
DEFAULT_EMBEDDING_CONCURRENCY = 1
DEFAULT_EMBEDDING_TEXT_CHARS = 4_000
DEFAULT_EMBEDDING_MAX_RETRIES = 2
_STORE_KEYS = {"model_id", "dimension", "dtype", "matrix_file", "records"}
_RECORD_KEYS = {"row", "key", "skill_id", "kind", "field_name", "text_hash"}
_EMBEDDING_KINDS = {"skill", "requires", "produces"}
_MATRIX_VALIDATION_CHUNK = 4096
_STORE_CACHE_LOCK = Lock()
_STORE_CACHE: dict[str, tuple[tuple[int, int, int, int], LoadedEmbeddingStore]] = {}


class EmbeddingProvider(Protocol):
    model_id: str
    dimension: int

    def embed(self, text: str) -> list[float]:
        """Embed one document or query."""


@dataclass(frozen=True, slots=True)
class LoadedEmbeddingStore:
    model_id: str
    dimension: int
    skill_ids: tuple[str, ...]
    skill_rows: tuple[int, ...]
    matrix: np.ndarray


@dataclass(slots=True)
class ApiEmbeddingProvider:
    model_id: str = DEFAULT_EMBEDDING_MODEL_ID
    dimension: int = DEFAULT_EMBEDDING_DIMENSION
    api_key: str = ""
    api_base: str = ""
    timeout: float = 120.0
    batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE
    concurrency: int = DEFAULT_EMBEDDING_CONCURRENCY
    max_text_chars: int = DEFAULT_EMBEDDING_TEXT_CHARS
    max_retries: int = DEFAULT_EMBEDDING_MAX_RETRIES

    def __post_init__(self) -> None:
        if not isinstance(self.model_id, str) or not self.model_id.strip():
            raise ValueError("model_id must be a non-empty string")
        _require_int(self.dimension, name="dimension", minimum=1)
        _require_float(self.timeout, name="timeout", minimum_exclusive=0.0)
        _require_int(self.batch_size, name="batch_size", minimum=1)
        _require_int(self.concurrency, name="concurrency", minimum=1)
        _require_int(self.max_text_chars, name="max_text_chars", minimum=0)
        _require_int(self.max_retries, name="max_retries", minimum=1)
        for name, value in (("api_key", self.api_key), ("api_base", self.api_base)):
            if not isinstance(value, str):
                raise ValueError(f"{name} must be a string")

    @classmethod
    def from_env(
        cls,
        *,
        env_path: str | Path | None = ".env",
        model_id: str | None = None,
        dimension: int | None = None,
    ) -> ApiEmbeddingProvider:
        values = read_env_file(env_path)
        resolved_model = (
            model_id
            if model_id is not None
            else _first_value(
                values,
                "EMBEDDING_MODEL",
                default=DEFAULT_EMBEDDING_MODEL_ID,
            )
        )
        return cls(
            model_id=resolved_model,
            dimension=(
                dimension
                if dimension is not None
                else _parse_int(
                    _first_value(
                        values,
                        "EMBEDDING_DIMENSION",
                        default=str(DEFAULT_EMBEDDING_DIMENSION),
                    ),
                    name="EMBEDDING_DIMENSION",
                )
            ),
            api_key=_first_value(values, "EMBEDDING_API_KEY")
            or _first_value(values, "API_KEY", "OPENAI_API_KEY"),
            api_base=_first_value(values, "EMBEDDING_BASE_URL")
            or _first_value(
                values,
                "BASE_URL",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
                default=DEFAULT_API_BASE,
            ),
            timeout=_parse_float(
                _first_value(values, "EMBEDDING_TIMEOUT", default="120"),
                name="EMBEDDING_TIMEOUT",
            ),
            batch_size=_parse_int(
                _first_value(
                    values,
                    "EMBEDDING_BATCH_SIZE",
                    default=str(DEFAULT_EMBEDDING_BATCH_SIZE),
                ),
                name="EMBEDDING_BATCH_SIZE",
            ),
            concurrency=_parse_int(
                _first_value(
                    values,
                    "EMBEDDING_CONCURRENCY",
                    default=str(DEFAULT_EMBEDDING_CONCURRENCY),
                ),
                name="EMBEDDING_CONCURRENCY",
            ),
            max_text_chars=_parse_int(
                _first_value(
                    values,
                    "EMBEDDING_TEXT_CHARS",
                    default=str(DEFAULT_EMBEDDING_TEXT_CHARS),
                ),
                name="EMBEDDING_TEXT_CHARS",
            ),
            max_retries=_parse_int(
                _first_value(
                    values,
                    "EMBEDDING_MAX_RETRIES",
                    default=str(DEFAULT_EMBEDDING_MAX_RETRIES),
                ),
                name="EMBEDDING_MAX_RETRIES",
            ),
        )

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - dependency smoke covers this path.
            raise RuntimeError("litellm is required for API embeddings") from exc
        vectors: list[list[float]] = []
        prepared = [_truncate_embedding_text(text, self.max_text_chars) for text in texts]
        batches = [
            prepared[start : start + self.batch_size]
            for start in range(0, len(prepared), self.batch_size)
        ]

        def embed_batch(batch: list[str]) -> list[list[float]]:
            kwargs: dict[str, Any] = {
                "model": self.model_id,
                "input": batch,
                "timeout": self.timeout,
                "request_timeout": self.timeout,
                "max_retries": self.max_retries,
            }
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base
            return _vectors_from_embedding_response(litellm.embedding(**kwargs))

        if self.concurrency == 1 or len(batches) == 1:
            embedded_batches = [embed_batch(batch) for batch in batches]
        else:
            with ThreadPoolExecutor(max_workers=min(self.concurrency, len(batches))) as executor:
                embedded_batches = list(executor.map(embed_batch, batches))
        for batch_vectors in embedded_batches:
            vectors.extend(batch_vectors)
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"embedding API returned {len(vectors)} vectors for {len(texts)} texts"
            )
        dimensions = {len(vector) for vector in vectors}
        if 0 in dimensions or len(dimensions) != 1:
            raise RuntimeError("embedding API returned empty or inconsistent vectors")
        actual_dimension = dimensions.pop()
        if self.dimension > 0 and actual_dimension != self.dimension:
            raise RuntimeError(
                f"embedding API returned dimension {actual_dimension}; expected {self.dimension}"
            )
        if any(not _is_finite_nonzero_vector(vector) for vector in vectors):
            raise RuntimeError("embedding API vectors must be finite and non-zero")
        self.dimension = actual_dimension
        return vectors


def default_embedding_provider(
    *,
    env_path: str | Path | None = ".env",
) -> EmbeddingProvider:
    return ApiEmbeddingProvider.from_env(env_path=env_path)


def embedding_provider_for_model(
    model_id: str,
    *,
    dimension: int,
    env_path: str | Path | None = ".env",
) -> EmbeddingProvider:
    if not model_id or dimension <= 0:
        raise ValueError("embedding store requires a model id and positive dimension")
    return ApiEmbeddingProvider.from_env(
        env_path=env_path,
        model_id=model_id,
        dimension=dimension,
    )


def load_skill_embedding_store(path: str | Path) -> LoadedEmbeddingStore:
    """Load the canonical memory-mapped embedding store used by query routing."""

    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"embedding store not found: {target}; rebuild the workspace")
    if target.is_symlink():
        raise ValueError(f"embedding store may not be a symlink: {target}")
    target = target.resolve()
    identity = target.stat()
    cache_key = str(target)
    cache_signature = (
        identity.st_dev,
        identity.st_ino,
        identity.st_size,
        identity.st_mtime_ns,
    )
    with _STORE_CACHE_LOCK:
        cached = _STORE_CACHE.get(cache_key)
        if cached is not None and cached[0] == cache_signature:
            return cached[1]
        store = _load_skill_embedding_store_uncached(target)
        _STORE_CACHE[cache_key] = (cache_signature, store)
        return store


def _load_skill_embedding_store_uncached(target: Path) -> LoadedEmbeddingStore:
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid embedding store JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != _STORE_KEYS:
        raise ValueError("embedding store must use the canonical binary fields")
    model_id = payload["model_id"]
    dimension = payload["dimension"]
    rows = payload["records"]
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("embedding store model_id must be a non-empty string")
    if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
        raise ValueError("embedding store dimension must be a positive integer")
    if payload["dtype"] != "float32":
        raise ValueError("embedding store dtype must be float32")
    matrix_name = payload["matrix_file"]
    expected_matrix_name = target.with_suffix(".npy").name
    if (
        not isinstance(matrix_name, str)
        or Path(matrix_name).name != matrix_name
        or matrix_name in {"", ".", ".."}
        or matrix_name != expected_matrix_name
    ):
        raise ValueError("embedding store matrix_file is unsafe or unexpected")
    matrix_path = target.parent / matrix_name
    if matrix_path.is_symlink():
        raise ValueError("embedding matrix may not be a symlink")
    if not matrix_path.is_file():
        raise FileNotFoundError(f"embedding matrix not found: {matrix_path}")
    if not isinstance(rows, list):
        raise ValueError("embedding store metadata is invalid")
    skill_ids: list[str] = []
    skill_rows: list[int] = []
    record_keys: set[str] = set()
    skill_key_ids: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != _RECORD_KEYS:
            raise ValueError(f"embedding store record {index} has invalid fields")
        record_row = row["row"]
        if isinstance(record_row, bool) or not isinstance(record_row, int) or record_row != index:
            raise ValueError("embedding store rows must be unique and contiguous")
        key = _required_store_string(row["key"], label=f"record {index} key")
        if key in record_keys:
            raise ValueError(f"embedding store contains duplicate record key: {key}")
        record_keys.add(key)
        skill_id = _required_store_string(row["skill_id"], label=f"record {index} skill_id")
        if not isinstance(row["field_name"], str):
            raise ValueError(f"embedding store record {index} field_name must be a string")
        _required_store_string(row["text_hash"], label=f"record {index} text_hash")
        kind = row["kind"]
        if kind not in _EMBEDDING_KINDS:
            raise ValueError(f"embedding store record {index} has invalid kind")
        if kind == "skill":
            if skill_id in skill_key_ids:
                raise ValueError(f"duplicate skill embedding: {skill_id}")
            skill_key_ids.add(skill_id)
            skill_ids.append(skill_id)
            skill_rows.append(index)
    try:
        matrix = np.load(matrix_path, mmap_mode="r", allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"failed to load embedding matrix: {exc}") from exc
    if (
        not isinstance(matrix, np.memmap)
        or matrix.dtype != np.dtype("float32")
        or matrix.ndim != 2
        or matrix.shape != (len(rows), dimension)
        or not matrix.flags.c_contiguous
    ):
        raise ValueError("embedding matrix shape, dtype, or layout is invalid")
    _validate_embedding_matrix(matrix)
    if not skill_ids:
        raise ValueError("embedding store contains no routable skill vectors")
    return LoadedEmbeddingStore(
        model_id=model_id,
        dimension=dimension,
        skill_ids=tuple(skill_ids),
        skill_rows=tuple(skill_rows),
        matrix=matrix,
    )


def write_binary_embedding_store(
    path: str | Path,
    *,
    model_id: str,
    records: list[Any],
) -> None:
    """Publish one canonical metadata plus float32 matrix store."""

    if not records:
        raise ValueError("embedding store requires at least one record")
    dimension = len(records[0].vector)
    if dimension <= 0:
        raise ValueError("embedding store dimension must be positive")
    target = Path(path)
    matrix_target = target.with_suffix(".npy")
    target.parent.mkdir(parents=True, exist_ok=True)
    matrix_temp = matrix_target.with_name(matrix_target.name + ".tmp")
    metadata_temp = target.with_name(target.name + ".tmp")
    if matrix_temp.exists() or metadata_temp.exists():
        raise ValueError("embedding store staging path already exists")
    matrix = np.lib.format.open_memmap(
        matrix_temp,
        mode="w+",
        dtype=np.float32,
        shape=(len(records), dimension),
    )
    metadata_rows: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    try:
        for row, record in enumerate(records):
            key = _required_store_string(record.key, label=f"record {row} key")
            if key in seen_keys:
                raise ValueError(f"embedding store contains duplicate record key: {key}")
            seen_keys.add(key)
            vector = np.asarray(record.vector, dtype=np.float32)
            if vector.ndim != 1 or vector.shape[0] != dimension:
                raise ValueError(f"embedding record {row} has the wrong vector shape")
            if not np.isfinite(vector).all() or not np.any(vector != 0.0):
                raise ValueError(f"embedding record {row} vector must be finite and non-zero")
            matrix[row] = vector
            metadata_rows.append(
                {
                    "row": row,
                    "key": key,
                    "skill_id": _required_store_string(
                        record.skill_id,
                        label=f"record {row} skill_id",
                    ),
                    "kind": record.kind,
                    "field_name": record.field_name,
                    "text_hash": _required_store_string(
                        record.text_hash,
                        label=f"record {row} text_hash",
                    ),
                }
            )
        matrix.flush()
        del matrix
        os.replace(matrix_temp, matrix_target)
        payload = {
            "model_id": _required_store_string(model_id, label="model_id"),
            "dimension": dimension,
            "dtype": "float32",
            "matrix_file": matrix_target.name,
            "records": metadata_rows,
        }
        metadata_temp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        os.replace(metadata_temp, target)
    except Exception:
        if matrix_temp.exists():
            matrix_temp.unlink()
        if metadata_temp.exists():
            metadata_temp.unlink()
        raise


def embed_query(provider: EmbeddingProvider, query: str) -> list[float]:
    embed_query_fn = getattr(provider, "embed_query", None)
    if callable(embed_query_fn):
        return list(map(float, embed_query_fn(query)))
    return list(map(float, provider.embed(query)))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm <= 0 or right_norm <= 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def _truncate_embedding_text(text: str, max_chars: int) -> str:
    if max_chars == 0 or len(text) <= max_chars:
        return text
    head = max(1, max_chars * 2 // 3)
    tail = max(1, max_chars - head)
    return text[:head].rstrip() + "\n\n...\n\n" + text[-tail:].lstrip()


def _vectors_from_embedding_response(response: Any) -> list[list[float]]:
    if hasattr(response, "model_dump"):
        response = response.model_dump()
    elif hasattr(response, "dict"):
        response = response.dict()
    data = response.get("data", []) if isinstance(response, dict) else getattr(response, "data", [])
    rows: list[tuple[int, list[float]]] = []
    for index, item in enumerate(data):
        if isinstance(item, dict):
            embedding = item.get("embedding", [])
            row_index = int(item.get("index", index))
        else:
            embedding = getattr(item, "embedding", [])
            row_index = int(getattr(item, "index", index))
        rows.append((row_index, [float(value) for value in embedding]))
    rows.sort(key=lambda item: item[0])
    if not rows:
        raise RuntimeError("embedding response did not contain vectors")
    if [row_index for row_index, _vector in rows] != list(range(len(rows))):
        raise RuntimeError("embedding response indexes must be unique and contiguous")
    return [vector for _index, vector in rows]


def _is_finite_nonzero_vector(vector: list[float]) -> bool:
    return all(math.isfinite(value) for value in vector) and any(value != 0.0 for value in vector)


def _validate_embedding_matrix(matrix: np.ndarray) -> None:
    for start in range(0, matrix.shape[0], _MATRIX_VALIDATION_CHUNK):
        chunk = matrix[start : start + _MATRIX_VALIDATION_CHUNK]
        if not np.isfinite(chunk).all():
            raise ValueError("embedding matrix contains non-finite values")
        if np.any(~np.any(chunk != 0.0, axis=1)):
            raise ValueError("embedding matrix contains a zero vector")


def _first_value(values: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        if values.get(key):
            return values[key]
    for key in keys:
        if os.environ.get(key):
            return os.environ[key]
    return default


def _parse_int(raw: str, *, name: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _required_store_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"embedding store {label} must be a non-empty string")
    return value.strip()


def _parse_float(raw: str, *, name: str) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc


def _require_int(value: object, *, name: str, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")
    return value


def _require_float(value: object, *, name: str, minimum_exclusive: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number greater than {minimum_exclusive}")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved <= minimum_exclusive:
        raise ValueError(f"{name} must be a finite number greater than {minimum_exclusive}")
    return resolved


__all__ = [
    "DEFAULT_EMBEDDING_MODEL_ID",
    "ApiEmbeddingProvider",
    "EmbeddingProvider",
    "LoadedEmbeddingStore",
    "cosine_similarity",
    "default_embedding_provider",
    "embed_query",
    "embedding_provider_for_model",
    "load_skill_embedding_store",
    "write_binary_embedding_store",
]
