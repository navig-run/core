"""Tests for navig.llm_routing_types — dataclasses, protocols, factory."""

from __future__ import annotations

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# ModelSelection
# ---------------------------------------------------------------------------


class TestModelSelection:
    def _make(self, **kwargs):
        from navig.llm_routing_types import ModelSelection

        defaults = dict(provider_name="openai", model_name="gpt-4o")
        defaults.update(kwargs)
        return ModelSelection(**defaults)

    def test_required_fields(self):
        sel = self._make()
        assert sel.provider_name == "openai"
        assert sel.model_name == "gpt-4o"

    def test_defaults(self):
        from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE
        from navig.llm_routing_types import ModelSelection

        sel = ModelSelection(provider_name="x", model_name="m")
        assert sel.temperature == _DEFAULT_TEMPERATURE
        assert sel.max_tokens == _DEFAULT_MAX_TOKENS
        assert sel.base_url == ""
        assert sel.api_key_env == ""
        assert sel.tier == ""
        assert sel.strategy_name == ""
        assert sel.is_uncensored is False
        assert sel.metadata == {}

    def test_custom_fields(self):
        sel = self._make(
            temperature=0.5,
            max_tokens=512,
            tier="premium",
            strategy_name="balanced",
            is_uncensored=True,
            metadata={"foo": "bar"},
        )
        assert sel.temperature == 0.5
        assert sel.max_tokens == 512
        assert sel.tier == "premium"
        assert sel.strategy_name == "balanced"
        assert sel.is_uncensored is True
        assert sel.metadata == {"foo": "bar"}

    def test_repr(self):
        sel = self._make(tier="free", strategy_name="quick")
        r = repr(sel)
        assert "openai" in r
        assert "gpt-4o" in r
        assert "tier=free" in r
        assert "strategy=quick" in r


# ---------------------------------------------------------------------------
# LLMResult
# ---------------------------------------------------------------------------


class TestLLMResult:
    def _make(self, **kwargs):
        from navig.llm_routing_types import LLMResult

        defaults = dict(content="hello")
        defaults.update(kwargs)
        return LLMResult(**defaults)

    def test_content_required(self):
        r = self._make(content="hi")
        assert r.content == "hi"

    def test_defaults(self):
        r = self._make()
        assert r.model == ""
        assert r.provider == ""
        assert r.latency_ms == 0
        assert r.prompt_tokens == 0
        assert r.completion_tokens == 0
        assert r.finish_reason == ""
        assert r.is_fallback is False
        assert r.attempts == 1
        assert r.selection is None
        assert r.raw == {}

    def test_total_tokens(self):
        r = self._make(prompt_tokens=30, completion_tokens=70)
        assert r.total_tokens == 100

    def test_total_tokens_zero_by_default(self):
        r = self._make()
        assert r.total_tokens == 0

    def test_with_selection(self):
        from navig.llm_routing_types import LLMResult, ModelSelection

        sel = ModelSelection(provider_name="openai", model_name="gpt-4o")
        r = LLMResult(content="x", selection=sel)
        assert r.selection is sel


# ---------------------------------------------------------------------------
# LLMChunk
# ---------------------------------------------------------------------------


class TestLLMChunk:
    def test_defaults(self):
        from navig.llm_routing_types import LLMChunk

        chunk = LLMChunk(content="delta")
        assert chunk.content == "delta"
        assert chunk.model == ""
        assert chunk.provider == ""
        assert chunk.finish_reason == ""
        assert chunk.raw == {}

    def test_all_fields(self):
        from navig.llm_routing_types import LLMChunk

        chunk = LLMChunk(
            content="x",
            model="gpt-4",
            provider="openai",
            finish_reason="stop",
            raw={"id": "abc"},
        )
        assert chunk.finish_reason == "stop"
        assert chunk.raw == {"id": "abc"}


# ---------------------------------------------------------------------------
# RoutingContext
# ---------------------------------------------------------------------------


class TestRoutingContext:
    def test_defaults(self):
        from navig.llm_routing_types import RoutingContext

        ctx = RoutingContext()
        assert ctx.user_input == ""
        assert ctx.messages == []
        assert ctx.mode_hint is None
        assert ctx.tier_override is None
        assert ctx.model_override is None
        assert ctx.provider_override is None
        assert ctx.prefer_uncensored is None
        assert ctx.temperature is None
        assert ctx.max_tokens is None
        assert ctx.stream is False
        assert ctx.timeout == 120.0
        assert ctx.metadata == {}

    def test_custom(self):
        from navig.llm_routing_types import RoutingContext

        ctx = RoutingContext(
            user_input="hello",
            mode_hint="coding",
            tier_override="premium",
            stream=True,
            timeout=30.0,
        )
        assert ctx.user_input == "hello"
        assert ctx.mode_hint == "coding"
        assert ctx.tier_override == "premium"
        assert ctx.stream is True
        assert ctx.timeout == 30.0

    def test_messages_mutable_default(self):
        from navig.llm_routing_types import RoutingContext

        a = RoutingContext()
        b = RoutingContext()
        a.messages.append({"role": "user", "content": "hi"})
        assert b.messages == [], "mutable default should not be shared"


# ---------------------------------------------------------------------------
# Protocol runtime checks
# ---------------------------------------------------------------------------


class TestProtocolChecks:
    def test_mode_router_protocol_satisfied_by_duck(self):
        from navig.llm_routing_types import ModeRouterProtocol

        class FakeRouter:
            def resolve_mode(self, hint: str) -> str:
                return hint

            def detect_mode(self, user_input: str) -> str:
                return "small_talk"

        assert isinstance(FakeRouter(), ModeRouterProtocol)

    def test_mode_router_protocol_not_satisfied_without_methods(self):
        from navig.llm_routing_types import ModeRouterProtocol

        class Incomplete:
            def resolve_mode(self, hint: str) -> str:
                return hint

        # Missing detect_mode
        assert not isinstance(Incomplete(), ModeRouterProtocol)

    def test_model_router_protocol_satisfied(self):
        from navig.llm_routing_types import (
            ModelRouterProtocol,
            ModelSelection,
            RoutingContext,
        )

        class FakeModelRouter:
            def select_model(self, mode: str, context: RoutingContext) -> ModelSelection:
                return ModelSelection(provider_name="x", model_name="y")

        assert isinstance(FakeModelRouter(), ModelRouterProtocol)

    def test_provider_factory_protocol_satisfied(self):
        from navig.llm_routing_types import LLMClientProtocol, ProviderFactoryProtocol

        class FakeFactory:
            def get_client(self, provider_name: str, **kwargs) -> LLMClientProtocol:
                return MagicMock()  # type: ignore[return-value]

        assert isinstance(FakeFactory(), ProviderFactoryProtocol)


# ---------------------------------------------------------------------------
# UnifiedProviderFactory
# ---------------------------------------------------------------------------


class TestUnifiedProviderFactory:
    def test_get_client_caches_result(self):
        from navig.llm_routing_types import UnifiedProviderFactory

        factory = UnifiedProviderFactory()
        mock_client = MagicMock()

        with patch.object(factory, "_create_client", return_value=mock_client) as m:
            c1 = factory.get_client("openai")
            c2 = factory.get_client("openai")

        assert c1 is c2
        m.assert_called_once()

    def test_different_providers_not_shared(self):
        from navig.llm_routing_types import UnifiedProviderFactory

        factory = UnifiedProviderFactory()
        clients = iter([MagicMock(), MagicMock()])

        with patch.object(factory, "_create_client", side_effect=lambda pn, **kw: next(clients)):
            c1 = factory.get_client("openai")
            c2 = factory.get_client("anthropic")

        assert c1 is not c2

    @pytest.mark.asyncio
    async def test_close_all_clears_cache(self):
        from navig.llm_routing_types import UnifiedProviderFactory

        factory = UnifiedProviderFactory()
        mock_client = AsyncMock()

        with patch.object(factory, "_create_client", return_value=mock_client):
            factory.get_client("openai")

        assert len(factory._cache) == 1
        await factory.close_all()
        assert factory._cache == {}

    @pytest.mark.asyncio
    async def test_close_all_swallows_errors(self):
        from navig.llm_routing_types import UnifiedProviderFactory

        factory = UnifiedProviderFactory()
        bad_client = AsyncMock()
        bad_client.close.side_effect = RuntimeError("oops")

        with patch.object(factory, "_create_client", return_value=bad_client):
            factory.get_client("openai")

        # Should not raise
        await factory.close_all()


# ---------------------------------------------------------------------------
# get_provider_factory singleton
# ---------------------------------------------------------------------------


class TestGetProviderFactory:
    def test_returns_singleton(self):
        import navig.llm_routing_types as m

        # Reset singleton for isolation
        m._factory = None
        from navig.llm_routing_types import get_provider_factory

        f1 = get_provider_factory()
        f2 = get_provider_factory()
        assert f1 is f2

    def test_singleton_thread_safe(self):
        import navig.llm_routing_types as m

        m._factory = None
        from navig.llm_routing_types import get_provider_factory

        results = []

        def worker():
            results.append(get_provider_factory())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(f is results[0] for f in results)
