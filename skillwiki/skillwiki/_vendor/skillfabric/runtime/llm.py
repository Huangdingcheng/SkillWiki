"""LiteLLM configuration and call wrappers."""

from __future__ import annotations

import json
import logging
import math
import multiprocessing as mp
import os
import queue
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from skillwiki._vendor.skillfabric.runtime.usage import LLMUsageRecord, LLMUsageTracker

# Keep default offline test/build paths from making LiteLLM fetch pricing metadata.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

DEFAULT_MODEL = "openai/responses/gpt-5.4-mini"
DEFAULT_API_BASE = "https://api.openai.com/v1"
DEFAULT_REASONING_EFFORT = "medium"
DEFAULT_MAX_TOKENS = 32768
DEFAULT_TIMEOUT = 120
DEFAULT_NETWORK_RETRIES = 2
TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 502, 503, 504})
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMUsageContext:
    """Thread-local usage overrides for nested SkillFabric runs."""

    log_path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


_USAGE_CONTEXT: ContextVar[LLMUsageContext | None] = ContextVar(
    "skillfabric_llm_usage_context",
    default=None,
)
_USAGE_BUFFER: ContextVar[list[tuple[Path, LLMUsageRecord]] | None] = ContextVar(
    "skillfabric_llm_usage_buffer",
    default=None,
)


@dataclass(slots=True)
class LLMUsageTransaction:
    """Finalize buffered usage after an LLM-backed operation is evaluated."""

    records: list[tuple[Path, LLMUsageRecord]] = field(default_factory=list, repr=False)
    committed: bool = False

    def commit(self) -> None:
        self.committed = True

    def reject(self, error: BaseException) -> None:
        """Persist billed calls while marking successful responses as locally rejected."""

        message = f"{type(error).__name__}: {error}"
        for _path, record in self.records:
            if record.status == "completed":
                record.status = "rejected"
                record.error = message
        self.committed = True


class LLMRequestError(RuntimeError):
    """Raised after LiteLLM exhausts its request-level retry policy."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type or type(self).__name__
        self.retryable = retryable


def _current_usage_context() -> LLMUsageContext:
    return _USAGE_CONTEXT.get() or LLMUsageContext()


@dataclass(slots=True)
class LLMConfig:
    """LLM configuration for SkillFabric workflows."""

    api_base: str
    api_key: str
    model: str = DEFAULT_MODEL
    credential_source: str = "api_key"
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        _require_string(self.api_base, name="api_base")
        _require_string(self.api_key, name="api_key")
        _require_string(self.model, name="model")
        if self.credential_source not in {
            "api_key",
            "anthropic_api_key",
            "anthropic_auth_token",
        }:
            raise ValueError("credential_source is unsupported")
        if not isinstance(self.reasoning_effort, str):
            raise ValueError("reasoning_effort must be a string")
        _require_positive_int(self.max_tokens, name="max_tokens")
        _require_nonnegative_float(self.timeout, name="timeout")

    @classmethod
    def from_env(
        cls,
        *,
        env_path: str | Path | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float | None = None,
    ) -> LLMConfig:
        """Read LiteLLM configuration from an env file and process environment."""

        values = read_env_file(env_path)
        configured_api_base, api_base_key = _first_config_entry(
            values,
            (
                "SKILLFABRIC_LLM_API_BASE",
                "LLM_BASE_URL",
                "BASE_URL",
                "OPENAI_BASE_URL",
                "OPENAI_API_BASE",
            ),
            ("ANTHROPIC_BASE_URL",),
        )
        configured_api_key, credential_key = _first_config_entry(
            values,
            ("SKILLFABRIC_LLM_API_KEY", "LLM_API_KEY", "API_KEY", "OPENAI_API_KEY"),
            ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
        )
        resolved_api_key = configured_api_key if api_key is None else api_key
        resolved_api_base = configured_api_base if api_base is None else api_base
        if api_key is not None:
            credential_key = "OPENAI_API_KEY"
        if api_base is not None:
            api_base_key = "OPENAI_BASE_URL"
        configured_model = model or _first_config_value(
            values,
            ("SKILLFABRIC_LLM_MODEL", "MODEL"),
            ("ANTHROPIC_MODEL",),
            default=DEFAULT_MODEL,
        )
        resolved_model = _normalize_litellm_model(
            configured_model,
            values=values,
            credential_key=credential_key,
            api_base_key=api_base_key,
        )
        resolved_api_base = _normalize_api_base(
            resolved_api_base or DEFAULT_API_BASE,
            model=resolved_model,
        )
        resolved_reasoning_effort = reasoning_effort or _first_value(
            values, "SKILLFABRIC_LLM_REASONING_EFFORT", default=DEFAULT_REASONING_EFFORT
        )
        max_tokens = int(
            _first_value(
                values, "SKILLFABRIC_LLM_MAX_TOKENS", "MAX_TOKENS", default=str(DEFAULT_MAX_TOKENS)
            )
        )
        resolved_timeout = (
            float(
                _first_value(
                    values,
                    "SKILLFABRIC_LLM_TIMEOUT",
                    "TIMEOUT",
                    default=str(DEFAULT_TIMEOUT),
                )
            )
            if timeout is None
            else timeout
        )
        if not resolved_api_key:
            raise ValueError(
                "missing API key. Set LLM_API_KEY, API_KEY, OPENAI_API_KEY, or ANTHROPIC_AUTH_TOKEN. "
                "Run `skillfabric init` to configure a local environment file."
            )
        return cls(
            api_base=resolved_api_base,
            api_key=resolved_api_key,
            model=resolved_model,
            credential_source=_credential_source(credential_key),
            reasoning_effort=resolved_reasoning_effort,
            max_tokens=max_tokens,
            timeout=resolved_timeout,
        )


@contextmanager
def llm_usage_context(
    *,
    log_path: str | Path | None = None,
    metadata: dict[str, Any] | None = None,
) -> Iterator[None]:
    """Temporarily override usage tracking for the current thread/context."""

    parent = _current_usage_context()
    child = LLMUsageContext(
        log_path=parent.log_path if log_path is None else Path(log_path).expanduser().resolve(),
        metadata={**parent.metadata, **(metadata or {})},
    )
    token = _USAGE_CONTEXT.set(child)
    try:
        yield
    finally:
        _USAGE_CONTEXT.reset(token)


@contextmanager
def llm_usage_transaction() -> Iterator[LLMUsageTransaction]:
    """Buffer usage records until the caller accepts or rejects the operation."""

    parent = _USAGE_BUFFER.get()
    buffer: list[tuple[Path, LLMUsageRecord]] = []
    token = _USAGE_BUFFER.set(buffer)
    transaction = LLMUsageTransaction(records=buffer)
    try:
        yield transaction
    except BaseException:
        _USAGE_BUFFER.reset(token)
        if transaction.committed:
            _commit_usage_records(parent, buffer)
        raise
    else:
        _USAGE_BUFFER.reset(token)
    if transaction.committed:
        _commit_usage_records(parent, buffer)


def _commit_usage_records(
    parent: list[tuple[Path, LLMUsageRecord]] | None,
    records: list[tuple[Path, LLMUsageRecord]],
) -> None:
    if parent is not None:
        parent.extend(records)
        return
    for path, record in records:
        try:
            LLMUsageTracker(log_path=path).append(record)
        except Exception as exc:  # noqa: BLE001,PERF203 - accounting is best effort.
            LOGGER.warning("usage_write_failed error_type=%s", type(exc).__name__)


def read_env_file(env_path: str | Path | None = ".env") -> dict[str, str]:
    """Read simple KEY=VALUE env files without mutating the process environment."""

    if env_path is None:
        return {}
    return _read_env_file(Path(env_path))


def litellm_completion(
    *,
    messages: list[dict[str, Any]],
    config: LLMConfig | None = None,
    env_path: str | Path | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    usage_operation: str | None = None,
    usage_metadata: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """Call LiteLLM with project configuration and network retries."""

    resolved = config or LLMConfig.from_env(env_path=env_path)
    resolved_model = resolved.model if model is None else _require_string(model, name="model")
    resolved_max_tokens = (
        resolved.max_tokens
        if max_tokens is None
        else _require_positive_int(max_tokens, name="max_tokens")
    )
    provider_messages = _provider_messages(resolved_model, messages)
    _ensure_provider_env(resolved, resolved_model)
    call_kwargs = {
        **kwargs,
        "model": resolved_model,
        "messages": provider_messages,
        "max_tokens": resolved_max_tokens,
        "api_base": resolved.api_base,
        "api_key": _provider_api_key(resolved, resolved_model),
        "max_retries": DEFAULT_NETWORK_RETRIES,
    }
    if resolved.timeout == 0:
        import httpx

        call_kwargs["timeout"] = httpx.Timeout(None)
    else:
        call_kwargs.update(
            timeout=resolved.timeout,
            request_timeout=resolved.timeout,
            force_timeout=resolved.timeout,
        )
    if resolved.reasoning_effort:
        if resolved_model.startswith("openai/responses/"):
            extra_body = dict(call_kwargs.get("extra_body") or {})
            reasoning = dict(extra_body.get("reasoning") or {})
            reasoning["effort"] = resolved.reasoning_effort
            extra_body["reasoning"] = reasoning
            call_kwargs["extra_body"] = extra_body
        else:
            call_kwargs["reasoning_effort"] = resolved.reasoning_effort
    usage_context = _current_usage_context()
    operation = usage_operation or "llm"
    metadata = {**usage_context.metadata, **(usage_metadata or {})}
    usage_log_path = usage_context.log_path
    started = time.monotonic()
    try:
        if _should_use_process_timeout(resolved.timeout):
            response = _completion_with_process_timeout(call_kwargs, resolved.timeout)
        else:
            response = _direct_litellm_completion(call_kwargs, resolved.timeout)
    except Exception as exc:
        _record_usage(
            model=resolved_model,
            messages=provider_messages,
            response=None,
            operation=operation,
            started=started,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            metadata=metadata,
            usage_log_path=usage_log_path,
        )
        status_code = _exception_status_code(exc)
        error_type = exc.error_type if isinstance(exc, LLMRequestError) else type(exc).__name__
        retryable = (
            exc.retryable
            if isinstance(exc, LLMRequestError)
            else status_code in TRANSIENT_HTTP_STATUS_CODES or _is_timeout_exception(exc)
        )
        raise LLMRequestError(
            str(exc) if isinstance(exc, LLMRequestError) else f"{type(exc).__name__}: {exc}",
            status_code=status_code,
            error_type=error_type,
            retryable=retryable,
        ) from exc
    _record_usage(
        model=resolved_model,
        messages=provider_messages,
        response=response,
        operation=operation,
        started=started,
        status="completed",
        error=None,
        metadata=metadata,
        usage_log_path=usage_log_path,
    )
    return response


def _direct_litellm_completion(call_kwargs: dict[str, Any], timeout: float) -> Any:
    import litellm

    litellm.suppress_debug_info = True
    if timeout > 0:
        litellm.request_timeout = timeout
    return litellm.completion(**call_kwargs)


def _exception_status_code(exc: BaseException) -> int | None:
    for candidate in _exception_chain(exc):
        for value in (
            getattr(candidate, "status_code", None),
            getattr(candidate, "http_status", None),
            getattr(getattr(candidate, "response", None), "status_code", None),
        ):
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _is_timeout_exception(exc: BaseException) -> bool:
    return any(
        isinstance(candidate, TimeoutError) or "timeout" in type(candidate).__name__.lower()
        for candidate in _exception_chain(exc)
    )


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _should_use_process_timeout(timeout: float) -> bool:
    if timeout <= 0:
        return False
    if os.environ.get("SKILLFABRIC_LLM_PROCESS_TIMEOUT", "1").lower() in {"0", "false", "no"}:
        return False
    if threading.current_thread() is threading.main_thread():
        return False
    return not _using_injected_litellm_module()


def _using_injected_litellm_module() -> bool:
    module = sys.modules.get("litellm")
    return module is not None and not hasattr(module, "__file__")


def _completion_with_process_timeout(call_kwargs: dict[str, Any], timeout: float) -> Any:
    ctx = mp.get_context("spawn")
    output: mp.Queue[dict[str, Any]] = ctx.Queue(maxsize=1)
    process = ctx.Process(target=_completion_process_worker, args=(call_kwargs, timeout, output))
    process.start()
    attempts = int(call_kwargs.get("max_retries", 0)) + 1
    grace_seconds = min(5.0, max(0.5, timeout * 0.1))
    timeout_budget = (timeout + grace_seconds) * attempts
    try:
        result = output.get(timeout=timeout_budget)
    except queue.Empty as exc:
        process.terminate()
        process.join(timeout=5.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=5.0)
        raise TimeoutError(
            f"LLM call exceeded process timeout budget of {timeout_budget:g} seconds"
        ) from exc
    finally:
        if process.is_alive():
            process.join(timeout=1.0)
    if not result.get("ok"):
        raise LLMRequestError(
            str(result.get("error") or "LLM subprocess failed"),
            status_code=result.get("status_code"),
            error_type=result.get("error_type"),
            retryable=result.get("retryable") is True,
        )
    return result.get("response")


def _completion_process_worker(
    call_kwargs: dict[str, Any],
    timeout: float,
    output: mp.Queue[dict[str, Any]],
) -> None:
    try:
        sleep_seconds = float(call_kwargs.pop("_skillfabric_test_sleep_seconds", 0.0) or 0.0)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)
        response = _direct_litellm_completion(call_kwargs, timeout)
        output.put({"ok": True, "response": response_to_jsonable(response)})
    except BaseException as exc:  # noqa: BLE001 - subprocess boundary normalizes failures.
        status_code = _exception_status_code(exc)
        output.put(
            {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "status_code": status_code,
                "error_type": type(exc).__name__,
                "retryable": (
                    status_code in TRANSIENT_HTTP_STATUS_CODES or _is_timeout_exception(exc)
                ),
            }
        )


def response_to_jsonable(response: Any) -> Any:
    """Convert a LiteLLM response object into a JSON-serializable payload."""

    if hasattr(response, "model_dump"):
        return response.model_dump()
    if hasattr(response, "dict"):
        return response.dict()
    try:
        json.dumps(response)
    except TypeError:
        return str(response)
    return response


def _record_usage(
    *,
    model: str,
    messages: list[dict[str, Any]],
    response: Any,
    operation: str,
    started: float,
    status: str,
    error: str | None,
    metadata: dict[str, Any] | None,
    usage_log_path: Path | None,
) -> None:
    if usage_log_path is None:
        return
    try:
        buffer = _USAGE_BUFFER.get()
        tracker = LLMUsageTracker(log_path=None if buffer is not None else usage_log_path)
        record = tracker.record_completion(
            model=model,
            messages=messages,
            response=response,
            operation=operation,
            duration_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error=error,
            metadata=metadata,
        )
        if buffer is not None:
            buffer.append((usage_log_path, record))
    except Exception:  # noqa: BLE001 - accounting must not break LLM calls.
        return


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _first_value(values: dict[str, str], *keys: str, default: str = "") -> str:
    for key in keys:
        if values.get(key):
            return values[key]
    for key in keys:
        if os.environ.get(key):
            return os.environ[key]
    return default


def _first_config_value(
    values: dict[str, str],
    primary_keys: tuple[str, ...],
    provider_keys: tuple[str, ...],
    *,
    default: str = "",
) -> str:
    for key in primary_keys:
        if values.get(key):
            return values[key]
    for key in primary_keys:
        if os.environ.get(key):
            return os.environ[key]
    for key in provider_keys:
        if values.get(key):
            return values[key]
    for key in provider_keys:
        if os.environ.get(key):
            return os.environ[key]
    return default


def _first_config_entry(
    values: dict[str, str],
    primary_keys: tuple[str, ...],
    provider_keys: tuple[str, ...],
) -> tuple[str, str]:
    for key in primary_keys:
        if values.get(key):
            return values[key], key
    for key in primary_keys:
        if os.environ.get(key):
            return os.environ[key], key
    for key in provider_keys:
        if values.get(key):
            return values[key], key
    for key in provider_keys:
        if os.environ.get(key):
            return os.environ[key], key
    return "", ""


def _credential_source(key: str) -> str:
    if key == "ANTHROPIC_AUTH_TOKEN":
        return "anthropic_auth_token"
    if key == "ANTHROPIC_API_KEY":
        return "anthropic_api_key"
    return "api_key"


def _normalize_litellm_model(
    model: str,
    *,
    values: dict[str, str],
    credential_key: str,
    api_base_key: str,
) -> str:
    """Normalize env model names for LiteLLM without changing explicit provider ids."""

    normalized = model.strip()
    if "/" in normalized:
        return normalized
    anthropic_model = _first_value(values, "ANTHROPIC_MODEL")
    anthropic_base = _first_value(values, "ANTHROPIC_BASE_URL")
    primary_config = credential_key in {
        "SKILLFABRIC_LLM_API_KEY",
        "LLM_API_KEY",
        "API_KEY",
        "OPENAI_API_KEY",
    } or api_base_key in {
        "SKILLFABRIC_LLM_API_BASE",
        "LLM_BASE_URL",
        "BASE_URL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
    }
    if primary_config:
        return f"openai/{normalized}"
    if (
        anthropic_base
        and credential_key.startswith("ANTHROPIC_")
        and _looks_like_anthropic_model(normalized)
    ):
        return f"anthropic/{normalized}"
    openai_model = _first_value(values, "MODEL")
    if anthropic_model and not openai_model and anthropic_base:
        if _looks_like_anthropic_model(normalized):
            return f"anthropic/{normalized}"
        return f"openai/{normalized}"
    if not _looks_like_anthropic_model(normalized):
        return f"openai/{normalized}"
    return f"anthropic/{normalized}"


def _looks_like_anthropic_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized.startswith(("claude-", "anthropic."))


def _normalize_api_base(api_base: str, *, model: str) -> str:
    """Normalize provider base URLs without exposing provider-specific details upstream."""

    normalized = str(api_base or DEFAULT_API_BASE).strip().rstrip("/")
    if model.startswith("openai/"):
        return _ensure_openai_v1_base(normalized)
    if model.startswith("anthropic/") and normalized.endswith("/v1"):
        return normalized[:-3]
    return normalized


def _ensure_openai_v1_base(api_base: str) -> str:
    parsed = urlsplit(api_base)
    path = parsed.path.rstrip("/")
    if path.endswith("/v1"):
        return urlunsplit(parsed._replace(path=path))
    for endpoint in ("/v1/chat/completions", "/v1/responses", "/v1/embeddings"):
        if path.endswith(endpoint):
            return urlunsplit(parsed._replace(path=path[: -len(endpoint)] + "/v1"))
    if "/v1/" in f"{path}/":
        return urlunsplit(parsed._replace(path=path))
    if not path or path == "/":
        return urlunsplit(parsed._replace(path="/v1"))
    return urlunsplit(parsed._replace(path=path))


def _provider_api_key(config: LLMConfig, model: str) -> str | None:
    """Return the api_key argument LiteLLM should receive for this provider."""

    if model.startswith("anthropic/") and config.credential_source == "anthropic_auth_token":
        return None
    return config.api_key


def _ensure_provider_env(config: LLMConfig, model: str) -> None:
    """Expose env-only credentials required by provider adapters."""

    if (
        model.startswith("anthropic/")
        and config.credential_source == "anthropic_auth_token"
        and not os.environ.get("ANTHROPIC_AUTH_TOKEN")
    ):
        os.environ["ANTHROPIC_AUTH_TOKEN"] = config.api_key


def _provider_messages(model: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize messages for providers that do not accept explicit system messages."""

    if not model.startswith("anthropic/"):
        return messages
    system_parts: list[str] = []
    non_system_messages: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            content = str(message.get("content", "")).strip()
            if content:
                system_parts.append(content)
            continue
        non_system_messages.append(dict(message))
    if not system_parts:
        return messages
    system_text = "\n\n".join(system_parts)
    if non_system_messages and non_system_messages[0].get("role") == "user":
        first = dict(non_system_messages[0])
        first_content = str(first.get("content", ""))
        first["content"] = f"{system_text}\n\n{first_content}" if first_content else system_text
        return [first, *non_system_messages[1:]]
    return [{"role": "user", "content": system_text}, *non_system_messages]


def _require_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_positive_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_positive_float(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved <= 0:
        raise ValueError(f"{name} must be a finite positive number")
    return resolved


def _require_nonnegative_float(value: object, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite non-negative number")
    resolved = float(value)
    if not math.isfinite(resolved) or resolved < 0:
        raise ValueError(f"{name} must be a finite non-negative number")
    return resolved
