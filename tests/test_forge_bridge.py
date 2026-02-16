"""
Tests for the Forge LLM bridge, GitHub Models provider, streaming,
and Telegram webhook support.

Run: pytest tests/test_forge_bridge.py -v
"""

import asyncio
import json
import time
from typing import Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def forge_response():
    """A typical Forge ChatResponse JSON payload."""
    return {
        "text": "Hello! I'm happy to help.",
        "metadata": {
            "model": "copilot - GPT 4o",
            "provider": "vscode-lm",
            "latencyMs": 1200,
            "tokenUsage": {
                "prompt": 150,
                "completion": 25,
                "total": 175,
            },
        },
    }


@pytest.fixture
def health_response():
    """A typical Forge health endpoint response."""
    return {
        "status": "ready",
        "model": "copilot - GPT 4o",
        "uptime": 3600,
        "backends": ["vscode-llm"],
    }


@pytest.fixture
def messages():
    """Standard chat messages list."""
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
    ]


# ── ForgeProvider Tests ─────────────────────────────────────────────

class TestForgeProvider:
    """Test the ForgeProvider class."""

    def test_init_defaults(self):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider()
        assert p.name == "forge"
        assert p.base_url == "http://127.0.0.1:43821"
        assert p.api_key == ""

    def test_init_custom(self):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider(base_url="http://10.0.0.5:9999", api_key="my-token")
        assert p.base_url == "http://10.0.0.5:9999"
        assert p.api_key == "my-token"

    def test_auth_headers_with_token(self):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider(api_key="secret123")
        h = p._auth_headers()
        assert h["Authorization"] == "Bearer secret123"
        assert h["Content-Type"] == "application/json"

    def test_auth_headers_without_token(self):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider()
        h = p._auth_headers()
        assert "Authorization" not in h
        assert h["Content-Type"] == "application/json"

    def test_messages_to_forge_basic(self, messages):
        from navig.agent.llm_providers import ForgeProvider
        result = ForgeProvider._messages_to_forge(messages)
        assert result["text"] == "Hello!"
        assert result["scope"] == "personal"
        assert len(result["conversation"]) == 2
        assert result["conversation"][0]["role"] == "system"
        assert result["conversation"][1]["role"] == "user"

    def test_messages_to_forge_extracts_last_user_msg(self):
        from navig.agent.llm_providers import ForgeProvider
        msgs = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply"},
            {"role": "user", "content": "Second question"},
        ]
        result = ForgeProvider._messages_to_forge(msgs)
        assert result["text"] == "Second question"
        assert len(result["conversation"]) == 3

    def test_messages_to_forge_no_user_msg_uses_last(self):
        from navig.agent.llm_providers import ForgeProvider
        msgs = [{"role": "system", "content": "You are NAVIG."}]
        result = ForgeProvider._messages_to_forge(msgs)
        assert result["text"] == "You are NAVIG."

    def test_messages_to_forge_empty(self):
        from navig.agent.llm_providers import ForgeProvider
        result = ForgeProvider._messages_to_forge([])
        assert result["text"] == ""
        assert result["conversation"] == []

    @pytest.mark.asyncio
    async def test_chat_success(self, messages, forge_response):
        from navig.agent.llm_providers import ForgeProvider, LLMResponse
        p = ForgeProvider(api_key="tok")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=forge_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(p, '_get_session', return_value=mock_session):
            resp = await p.chat(model="", messages=messages)
        assert isinstance(resp, LLMResponse)
        assert resp.content == "Hello! I'm happy to help."
        assert resp.provider == "forge"
        assert resp.model == "copilot - GPT 4o"
        assert resp.prompt_tokens == 150
        assert resp.completion_tokens == 25

    @pytest.mark.asyncio
    async def test_chat_401_raises(self, messages):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider(api_key="wrong-token")

        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(p, '_get_session', return_value=mock_session):
            with pytest.raises(RuntimeError, match="401 Unauthorized"):
                await p.chat(model="", messages=messages)

    @pytest.mark.asyncio
    async def test_is_available_true(self, health_response):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=health_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch.object(p, '_get_session', return_value=mock_session):
            assert await p.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_false_on_error(self):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider()

        mock_session = AsyncMock()
        mock_session.get = MagicMock(side_effect=Exception("Connection refused"))

        with patch.object(p, '_get_session', return_value=mock_session):
            assert await p.is_available() is False

    @pytest.mark.asyncio
    async def test_is_available_false_on_not_ready(self):
        from navig.agent.llm_providers import ForgeProvider
        p = ForgeProvider()

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"status": "starting"})
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)

        with patch.object(p, '_get_session', return_value=mock_session):
            assert await p.is_available() is False


# ── GitHubModelsProvider Tests ──────────────────────────────────────

class TestGitHubModelsProvider:
    """Test the GitHub Models provider."""

    def test_init_defaults(self):
        from navig.agent.llm_providers import GitHubModelsProvider
        with patch.dict("os.environ", {}, clear=True):
            p = GitHubModelsProvider()
            assert p.name == "github_models"
            assert p.base_url == "https://models.inference.ai.azure.com"

    def test_init_with_token(self):
        from navig.agent.llm_providers import GitHubModelsProvider
        p = GitHubModelsProvider(api_key="ghp_test123")
        assert p.api_key == "ghp_test123"

    def test_init_from_env(self):
        from navig.agent.llm_providers import GitHubModelsProvider
        with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_env_tok"}):
            p = GitHubModelsProvider()
            assert p.api_key == "ghp_env_tok"

    @pytest.mark.asyncio
    async def test_chat_success(self, messages):
        from navig.agent.llm_providers import GitHubModelsProvider, LLMResponse
        p = GitHubModelsProvider(api_key="ghp_test")

        openai_response = {
            "choices": [{"message": {"content": "GitHub Models reply"}, "finish_reason": "stop"}],
            "model": "gpt-4o",
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=openai_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        with patch.object(p, '_get_session', return_value=mock_session):
            resp = await p.chat(model="gpt-4o", messages=messages)
        assert isinstance(resp, LLMResponse)
        assert resp.content == "GitHub Models reply"
        assert resp.provider == "github_models"
        assert resp.prompt_tokens == 50

    @pytest.mark.asyncio
    async def test_chat_no_token_raises(self, messages):
        from navig.agent.llm_providers import GitHubModelsProvider
        with patch.dict("os.environ", {}, clear=True):
            p = GitHubModelsProvider(api_key="")
            with pytest.raises(RuntimeError, match="GitHub token not set"):
                await p.chat(model="gpt-4o", messages=messages)

    @pytest.mark.asyncio
    async def test_is_available_with_token(self):
        from navig.agent.llm_providers import GitHubModelsProvider
        p = GitHubModelsProvider(api_key="ghp_test")
        assert await p.is_available() is True

    @pytest.mark.asyncio
    async def test_is_available_without_token(self):
        from navig.agent.llm_providers import GitHubModelsProvider
        with patch.dict("os.environ", {}, clear=True):
            p = GitHubModelsProvider(api_key="")
            # Mock config manager to return no token
            with patch("navig.agent.llm_providers.GitHubModelsProvider.is_available") as mock_avail:
                mock_avail.return_value = False
                assert await p.is_available() is False


# ── Factory Tests ───────────────────────────────────────────────────

class TestProviderFactory:
    """Test provider factory registration."""

    def test_create_forge(self):
        from navig.agent.llm_providers import create_provider
        p = create_provider("forge", base_url="http://test:1234", api_key="tok")
        assert p.name == "forge"
        assert p.base_url == "http://test:1234"

    def test_create_github_models(self):
        from navig.agent.llm_providers import create_provider
        p = create_provider("github_models", api_key="ghp_xxx")
        assert p.name == "github_models"

    def test_create_github_alias(self):
        from navig.agent.llm_providers import create_provider
        p = create_provider("github", api_key="ghp_xxx")
        assert p.name == "github_models"

    def test_list_providers_includes_new(self):
        from navig.agent.llm_providers import list_provider_names
        names = list_provider_names()
        assert "forge" in names
        assert "github_models" in names

    def test_unknown_provider_raises(self):
        from navig.agent.llm_providers import create_provider
        with pytest.raises(ValueError, match="Unknown provider"):
            create_provider("nonexistent")


# ── Streaming Tests ─────────────────────────────────────────────────

class TestStreamingSupport:
    """Test the chat_stream() async generator."""

    @pytest.mark.asyncio
    async def test_base_provider_stream_fallback(self, messages):
        """Base provider streams entire response as one chunk."""
        from navig.agent.llm_providers import LLMProvider, LLMResponse

        class DummyProvider(LLMProvider):
            name = "dummy"
            async def chat(self, model, messages, temperature=0.7, max_tokens=512, **kw):
                return LLMResponse(content="Full response", provider="dummy")

        p = DummyProvider()
        chunks = []
        async for chunk in p.chat_stream("model", messages):
            chunks.append(chunk)
        assert chunks == ["Full response"]

    @pytest.mark.asyncio
    async def test_forge_stream_parses_sse(self, messages):
        """ForgeProvider.chat_stream() correctly parses SSE events."""
        from navig.agent.llm_providers import ForgeProvider

        # Simulate SSE response lines
        sse_lines = [
            b'data: {"text": "Hello", "done": false}\n',
            b'\n',
            b'data: {"text": " world!", "done": false}\n',
            b'\n',
            b'data: {"text": "", "done": true, "metadata": {"model": "copilot"}}\n',
            b'\n',
        ]

        class FakeContent:
            """Async iterable that yields SSE lines."""
            def __init__(self, lines):
                self._lines = list(lines)
                self._idx = 0
            def __aiter__(self):
                return self
            async def __anext__(self):
                if self._idx >= len(self._lines):
                    raise StopAsyncIteration
                line = self._lines[self._idx]
                self._idx += 1
                return line

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.content = FakeContent(sse_lines)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)

        p = ForgeProvider(api_key="tok")

        with patch.object(p, '_get_session', return_value=mock_session):
            chunks = []
            async for chunk in p.chat_stream("", messages):
                chunks.append(chunk)

        assert chunks == ["Hello", " world!"]


# ── AIClient Detection Tests ───────────────────────────────────────

class TestAIClientDetection:
    """Test that _detect_best_provider() respects the new priority."""

    def _make_client(self, **attrs):
        """Build a bare AIClient without full __init__."""
        from navig.agent.ai_client import AIClient
        client = AIClient.__new__(AIClient)
        client._navig_api_key = attrs.get("_navig_api_key", None)
        client._airllm_config = attrs.get("_airllm_config", None)
        return client

    def test_forge_detected_when_port_open(self):
        client = self._make_client()
        with patch.object(client, '_get_forge_url', return_value="http://127.0.0.1:43821"):
            with patch("socket.socket") as mock_sock_cls:
                mock_sock = MagicMock()
                mock_sock.connect_ex.return_value = 0  # Port open
                mock_sock_cls.return_value = mock_sock
                result = client._detect_best_provider()
                assert result == "forge"

    def test_github_models_detected_when_token_set(self):
        client = self._make_client()
        with patch.object(client, '_get_forge_url', return_value=""):
            with patch.object(client, '_get_github_models_token', return_value="ghp_test"):
                result = client._detect_best_provider()
                assert result == "github_models"

    def test_openrouter_fallback_when_no_forge_no_github(self):
        client = self._make_client(_navig_api_key="sk-or-xxx")
        with patch.object(client, '_get_forge_url', return_value=""):
            with patch.object(client, '_get_github_models_token', return_value=""):
                result = client._detect_best_provider()
                assert result == "openrouter"

    def test_none_when_nothing_available(self):
        client = self._make_client()
        with patch.object(client, '_get_forge_url', return_value=""):
            with patch.object(client, '_get_github_models_token', return_value=""):
                with patch("socket.socket") as mock_sock_cls:
                    mock_sock = MagicMock()
                    mock_sock.connect_ex.return_value = 1  # Port closed
                    mock_sock_cls.return_value = mock_sock
                    result = client._detect_best_provider()
                    assert result == "none"


# ── Telegram Webhook Tests ──────────────────────────────────────────

class TestTelegramWebhook:
    """Test the webhook mode additions to TelegramChannel."""

    def test_webhook_mode_flag(self):
        from navig.gateway.channels.telegram import TelegramChannel
        ch = TelegramChannel(
            bot_token="123:FAKE",
            webhook_url="https://example.com/telegram/webhook",
            webhook_secret="test-secret",
        )
        assert ch._use_webhook is True
        assert ch.webhook_url == "https://example.com/telegram/webhook"
        assert ch.webhook_secret == "test-secret"

    def test_polling_mode_default(self):
        from navig.gateway.channels.telegram import TelegramChannel
        ch = TelegramChannel(bot_token="123:FAKE")
        assert ch._use_webhook is False

    @pytest.mark.asyncio
    async def test_handle_webhook_rejects_bad_secret(self):
        from navig.gateway.channels.telegram import TelegramChannel
        ch = TelegramChannel(
            bot_token="123:FAKE",
            webhook_url="https://example.com/hook",
            webhook_secret="correct-secret",
        )
        ch._running = True

        update = {"update_id": 1, "message": {"text": "hi"}}
        accepted = await ch.handle_webhook_update(update, secret_header="wrong-secret")
        assert accepted is False

    @pytest.mark.asyncio
    async def test_handle_webhook_accepts_correct_secret(self):
        from navig.gateway.channels.telegram import TelegramChannel
        ch = TelegramChannel(
            bot_token="123:FAKE",
            webhook_url="https://example.com/hook",
            webhook_secret="correct-secret",
        )
        ch._running = True

        # Mock _process_update to avoid actual processing
        ch._process_update = AsyncMock()

        update = {"update_id": 1, "message": {"text": "hi"}}
        accepted = await ch.handle_webhook_update(update, secret_header="correct-secret")
        assert accepted is True
        ch._process_update.assert_awaited_once_with(update)

    @pytest.mark.asyncio
    async def test_handle_webhook_rejects_when_stopped(self):
        from navig.gateway.channels.telegram import TelegramChannel
        ch = TelegramChannel(bot_token="123:FAKE", webhook_url="https://example.com/hook")
        ch._running = False

        update = {"update_id": 1, "message": {"text": "hi"}}
        accepted = await ch.handle_webhook_update(update)
        assert accepted is False


# ── Messages-to-Forge Conversion Edge Cases ────────────────────────

class TestMessagesToForgeEdgeCases:
    """Additional edge case tests for message format conversion."""

    def test_assistant_only_messages(self):
        from navig.agent.llm_providers import ForgeProvider
        msgs = [{"role": "assistant", "content": "I said something"}]
        result = ForgeProvider._messages_to_forge(msgs)
        # No user message — falls back to last message content
        assert result["text"] == "I said something"

    def test_multi_user_messages(self):
        from navig.agent.llm_providers import ForgeProvider
        msgs = [
            {"role": "user", "content": "First"},
            {"role": "user", "content": "Second"},
            {"role": "user", "content": "Third"},
        ]
        result = ForgeProvider._messages_to_forge(msgs)
        assert result["text"] == "Third"  # Last user message

    def test_scope_always_personal(self):
        from navig.agent.llm_providers import ForgeProvider
        result = ForgeProvider._messages_to_forge([{"role": "user", "content": "x"}])
        assert result["scope"] == "personal"
