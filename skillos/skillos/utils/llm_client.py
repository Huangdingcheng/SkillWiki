"""LLM 客户端封装 - 生产级别（重试、超时、流式、连通性测试）"""

from __future__ import annotations

import time
from typing import Any, Dict, Generator, Iterator, List, Optional

import httpx

from ..config.llm_config import LLMConfig
from .logger import get_logger

logger = get_logger(__name__)


class LLMError(Exception):
    """LLM 调用基础异常"""


class LLMAuthError(LLMError):
    """认证失败（401/403）"""


class LLMRateLimitError(LLMError):
    """触发限速（429）"""


class LLMTimeoutError(LLMError):
    """请求超时"""


class LLMServerError(LLMError):
    """服务端错误（5xx）"""


class Message:
    """聊天消息"""

    def __init__(self, role: str, content: str) -> None:
        self.role = role
        self.content = content

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def system(cls, content: str) -> "Message":
        return cls("system", content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls("user", content)

    @classmethod
    def assistant(cls, content: str) -> "Message":
        return cls("assistant", content)


class LLMResponse:
    """LLM 响应"""

    def __init__(
        self,
        content: str,
        model: str,
        usage: Optional[Dict[str, int]] = None,
        finish_reason: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.content = content
        self.model = model
        self.usage = usage or {}
        self.finish_reason = finish_reason
        self.raw = raw or {}

    @property
    def prompt_tokens(self) -> int:
        return self.usage.get("prompt_tokens", 0)

    @property
    def completion_tokens(self) -> int:
        return self.usage.get("completion_tokens", 0)

    @property
    def total_tokens(self) -> int:
        return self.usage.get("total_tokens", 0)

    def __repr__(self) -> str:
        return (
            f"LLMResponse(model={self.model!r}, "
            f"tokens={self.total_tokens}, "
            f"finish_reason={self.finish_reason!r})"
        )


class LLMClient:
    """
    LLM HTTP 客户端。

    - 兼容 OpenAI Chat Completions API（/v1/chat/completions）
    - 支持指数退避重试
    - 支持流式输出
    - 支持连通性测试
    """

    def __init__(self, config: LLMConfig) -> None:
        self._cfg = config
        self._base_url = config.api_url.rstrip("/")
        self._chat_url = _resolve_chat_completions_url(self._base_url)
        self._headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: List[Message],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        stream: Optional[bool] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> LLMResponse:
        """
        发送聊天请求，返回完整响应。

        Args:
            messages: 消息列表
            model: 覆盖配置中的模型名称
            temperature: 覆盖配置中的温度
            max_tokens: 覆盖配置中的 max_tokens
            stream: 覆盖配置中的流式设置
            extra: 额外的请求参数

        Returns:
            LLMResponse
        """
        payload = self._build_payload(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, stream=False, extra=extra,
        )
        raw = self._request_with_retry(payload)
        return self._parse_response(raw)

    def stream_chat(
        self,
        messages: List[Message],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Iterator[str]:
        """
        发送流式聊天请求，逐块 yield 文本片段。

        Args:
            messages: 消息列表

        Yields:
            str: 文本片段
        """
        payload = self._build_payload(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, stream=True, extra=extra,
        )
        yield from self._stream_request(payload)

    def ping(self) -> bool:
        """
        测试 API 连通性。

        Returns:
            bool: True 表示连通，False 表示失败
        """
        try:
            self.chat(
                [Message.user("ping")],
                max_tokens=1,
                temperature=0.0,
            )
            return True
        except LLMAuthError:
            raise  # 认证错误需要上层处理
        except Exception as e:
            logger.warning("LLM ping 失败: %s", e)
            return False

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: List[Message],
        *,
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool,
        extra: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": model or self._cfg.model,
            "messages": [m.to_dict() for m in messages],
            "temperature": temperature if temperature is not None else self._cfg.temperature,
            "max_tokens": max_tokens or self._cfg.max_tokens,
            "stream": stream,
        }
        if extra:
            payload.update(extra)
        return payload

    def _request_with_retry(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """带指数退避重试的 HTTP 请求。"""
        last_exc: Optional[Exception] = None
        delay = self._cfg.retry_delay

        for attempt in range(self._cfg.retry_count + 1):
            try:
                return self._do_request(payload)
            except LLMAuthError:
                raise  # 认证错误不重试
            except LLMRateLimitError as e:
                last_exc = e
                wait = delay * (2 ** attempt)
                logger.warning("触发限速，%.1f 秒后重试（第 %d 次）", wait, attempt + 1)
                time.sleep(wait)
            except (LLMTimeoutError, LLMServerError) as e:
                last_exc = e
                if attempt < self._cfg.retry_count:
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        "请求失败 (%s)，%.1f 秒后重试（第 %d 次）",
                        type(e).__name__, wait, attempt + 1,
                    )
                    time.sleep(wait)
            except LLMError:
                raise

        raise last_exc or LLMError("所有重试均失败")

    def _do_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            with httpx.Client(timeout=self._cfg.timeout) as client:
                resp = client.post(self._chat_url, json=payload, headers=self._headers)
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"请求超时（{self._cfg.timeout}s）: {e}") from e
        except httpx.RequestError as e:
            raise LLMError(f"网络请求失败: {e}") from e

        self._raise_for_status(resp)
        return resp.json()

    def _stream_request(self, payload: Dict[str, Any]) -> Iterator[str]:
        try:
            with httpx.Client(timeout=self._cfg.timeout) as client:
                with client.stream("POST", self._chat_url, json=payload, headers=self._headers) as resp:
                    self._raise_for_status(resp)
                    for line in resp.iter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            import json  # noqa: PLC0415
                            try:
                                chunk = json.loads(line[6:])
                                delta = chunk["choices"][0].get("delta", {})
                                text = delta.get("content", "")
                                if text:
                                    yield text
                            except (KeyError, ValueError):
                                continue
        except httpx.TimeoutException as e:
            raise LLMTimeoutError(f"流式请求超时: {e}") from e
        except httpx.RequestError as e:
            raise LLMError(f"流式请求网络失败: {e}") from e

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code == 401:
            raise LLMAuthError(f"认证失败（401）：API key 无效或已过期")
        if resp.status_code == 403:
            raise LLMAuthError(f"权限不足（403）：无权访问该资源")
        if resp.status_code == 429:
            raise LLMRateLimitError(f"触发限速（429）：请求过于频繁")
        if resp.status_code >= 500:
            raise LLMServerError(f"服务端错误（{resp.status_code}）：{resp.text[:200]}")
        if resp.status_code >= 400:
            raise LLMError(f"请求错误（{resp.status_code}）：{resp.text[:200]}")

    @staticmethod
    def _parse_response(raw: Dict[str, Any]) -> LLMResponse:
        try:
            choice = raw["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            usage = raw.get("usage", {})
            model = raw.get("model", "unknown")
            return LLMResponse(
                content=content,
                model=model,
                usage=usage,
                finish_reason=finish_reason,
                raw=raw,
            )
        except (KeyError, IndexError) as e:
            raise LLMError(f"解析响应失败: {e}，原始响应: {raw}") from e


def create_client(config: LLMConfig) -> LLMClient:
    """工厂函数：根据 LLMConfig 创建 LLMClient。"""
    return LLMClient(config)


def _resolve_chat_completions_url(api_url: str) -> str:
    """Return the concrete Chat Completions endpoint for common OpenAI-compatible bases."""
    base = api_url.rstrip("/")
    lowered = base.lower()
    if lowered.endswith("/chat/completions"):
        return base
    if lowered.endswith("/v1") or lowered.endswith("/beta"):
        return f"{base}/chat/completions"
    if "api.deepseek.com" in lowered:
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"
