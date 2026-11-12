"""Token counting for LLM contexts."""

from __future__ import annotations

import json
import math
from typing import Any


def count_message_tokens(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
) -> int:
    """Count tokens with LiteLLM, falling back to a conservative local estimate."""

    if model:
        try:
            import litellm

            return max(0, int(litellm.token_counter(model=model, messages=messages)))
        except Exception:  # noqa: BLE001 - counting must not block a valid request.
            pass
    return estimate_token_count(_messages_to_text(messages))


def estimate_token_count(text: str) -> int:
    """Estimate tokens conservatively when provider tokenization is unavailable."""

    stripped = text.strip()
    if not stripped:
        return 0
    word_estimate = len(stripped.split())
    char_estimate = math.ceil(len(stripped) / 4)
    return max(1, word_estimate, char_estimate)


def _messages_to_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role is not None:
            parts.append(str(role))
        parts.append(content_to_text(content))
    return "\n".join(part for part in parts if part)


def content_to_text(content: Any) -> str:
    """Flatten one provider message content value for token estimation."""

    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("text") is not None:
                    parts.append(str(item["text"]))
                elif item.get("content") is not None:
                    parts.append(content_to_text(item["content"]))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if isinstance(content, dict):
        if content.get("text") is not None:
            return str(content["text"])
        return json.dumps(_to_jsonable(content), ensure_ascii=False, sort_keys=True)
    return str(content)


def _to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_to_jsonable(item) for item in value]
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


__all__ = ["content_to_text", "count_message_tokens", "estimate_token_count"]
