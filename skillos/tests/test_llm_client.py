"""LLM 客户端测试"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from skillos.config import LLMConfig
from skillos.utils.llm_client import (
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    Message,
    LLMResponse,
    create_client,
    _resolve_chat_completions_url,
)


@pytest.fixture
def llm_config() -> LLMConfig:
    return LLMConfig(
        api_url="https://yunwu.ai",
        model="gpt-5.4-nano",
        api_key="test_key",
        temperature=0.7,
        max_tokens=100,
        timeout=10,
        retry_count=2,
        retry_delay=0.01,  # 测试时极短延迟
    )


@pytest.fixture
def client(llm_config: LLMConfig) -> LLMClient:
    return LLMClient(llm_config)


@pytest.fixture
def mock_response() -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gpt-5.4-nano",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello!"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }


class TestMessage:

    def test_user_message(self):
        m = Message.user("hello")
        assert m.role == "user"
        assert m.content == "hello"
        assert m.to_dict() == {"role": "user", "content": "hello"}

    def test_system_message(self):
        m = Message.system("You are helpful.")
        assert m.role == "system"

    def test_assistant_message(self):
        m = Message.assistant("I can help.")
        assert m.role == "assistant"


class TestLLMResponse:

    def test_token_properties(self):
        resp = LLMResponse(
            content="hi",
            model="gpt-5.4-nano",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )
        assert resp.prompt_tokens == 10
        assert resp.completion_tokens == 5
        assert resp.total_tokens == 15

    def test_empty_usage(self):
        resp = LLMResponse(content="hi", model="m")
        assert resp.total_tokens == 0


class TestLLMClient:

    def test_create_client(self, llm_config: LLMConfig):
        c = create_client(llm_config)
        assert isinstance(c, LLMClient)

    def test_chat_success(self, client: LLMClient, mock_response: dict):
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            mock_client_cls.return_value = mock_http

            result = client.chat([Message.user("hi")])

        assert isinstance(result, LLMResponse)
        assert result.content == "Hello!"
        assert result.total_tokens == 15

    def test_chat_auth_error(self, client: LLMClient):
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = mock_resp
            mock_client_cls.return_value = mock_http

            with pytest.raises(LLMAuthError):
                client.chat([Message.user("hi")])

    def test_chat_rate_limit_retries(self, client: LLMClient, mock_response: dict):
        import httpx
        rate_limit_resp = MagicMock(spec=httpx.Response)
        rate_limit_resp.status_code = 429
        rate_limit_resp.text = "Too Many Requests"

        ok_resp = MagicMock(spec=httpx.Response)
        ok_resp.status_code = 200
        ok_resp.json.return_value = mock_response

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return rate_limit_resp
            return ok_resp

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.side_effect = side_effect
            mock_client_cls.return_value = mock_http

            result = client.chat([Message.user("hi")])

        assert result.content == "Hello!"
        assert call_count == 3  # 2 次失败 + 1 次成功

    def test_chat_server_error_exhausts_retries(self, client: LLMClient):
        import httpx
        server_err = MagicMock(spec=httpx.Response)
        server_err.status_code = 500
        server_err.text = "Internal Server Error"

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.return_value = server_err
            mock_client_cls.return_value = mock_http

            with pytest.raises(LLMServerError):
                client.chat([Message.user("hi")])

    def test_chat_timeout(self, client: LLMClient):
        import httpx

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.side_effect = httpx.TimeoutException("timeout")
            mock_client_cls.return_value = mock_http

            with pytest.raises(LLMTimeoutError):
                client.chat([Message.user("hi")])

    def test_payload_model_override(self, client: LLMClient, mock_response: dict):
        import httpx
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_response

        captured_payload = {}

        def capture_post(url, json, headers):
            captured_payload.update(json)
            return mock_resp

        with patch("httpx.Client") as mock_client_cls:
            mock_http = MagicMock()
            mock_http.__enter__ = MagicMock(return_value=mock_http)
            mock_http.__exit__ = MagicMock(return_value=False)
            mock_http.post.side_effect = capture_post
            mock_client_cls.return_value = mock_http

            client.chat([Message.user("hi")], model="gpt-5.4-turbo")

        assert captured_payload["model"] == "gpt-5.4-turbo"

    def test_default_openai_compatible_chat_endpoint(self):
        assert (
            _resolve_chat_completions_url("https://yunwu.ai")
            == "https://yunwu.ai/v1/chat/completions"
        )

    def test_deepseek_base_url_uses_deepseek_chat_endpoint(self):
        assert (
            _resolve_chat_completions_url("https://api.deepseek.com")
            == "https://api.deepseek.com/chat/completions"
        )

    def test_v1_and_full_chat_endpoint_are_not_double_appended(self):
        assert (
            _resolve_chat_completions_url("https://api.deepseek.com/v1")
            == "https://api.deepseek.com/v1/chat/completions"
        )
        assert (
            _resolve_chat_completions_url("https://api.deepseek.com/chat/completions")
            == "https://api.deepseek.com/chat/completions"
        )
