"""Tests for LLM provider interfaces and routing types."""

import pytest

from navig.llm_routing_types import (
    LLMChunk,
    LLMProviderAdapter,
    LLMResult,
    ModelSelection,
    ModeRouterProtocol,
    RoutingContext,
    UnifiedProviderFactory,
    get_provider_factory,
)

# ---- Data structure tests ----


class TestModelSelection:
    def test_creation(self):
        ms = ModelSelection(
            provider_name="ollama",
            model_name="qwen2.5:3b",
            temperature=0.8,
            max_tokens=1024,
            tier="small",
            strategy_name="heuristic",
        )
        assert ms.provider_name == "ollama"
        assert ms.model_name == "qwen2.5:3b"
        assert ms.tier == "small"
        assert ms.strategy_name == "heuristic"
        assert ms.is_uncensored is False
        assert ms.metadata == {}

    def test_repr(self):
        ms = ModelSelection(
            provider_name="openai",
            model_name="gpt-4o",
            tier="big",
            strategy_name="llm_router",
        )
        r = repr(ms)
        assert "openai" in r
        assert "gpt-4o" in r
        assert "big" in r

    def test_defaults(self):
        ms = ModelSelection(provider_name="x", model_name="y")
        assert ms.temperature == 0.7
        assert ms.max_tokens == 4096
        assert ms.base_url == ""


class TestLLMResult:
    def test_creation(self):
        r = LLMResult(
            content="Hello world",
            model="gpt-4o",
            provider="openai",
            latency_ms=150,
            prompt_tokens=10,
            completion_tokens=5,
        )
        assert r.content == "Hello world"
        assert r.total_tokens == 15
        assert r.is_fallback is False
        assert r.attempts == 1

    def test_fallback_result(self):
        r = LLMResult(
            content="ok",
            model="gpt-3.5",
            is_fallback=True,
            attempts=3,
        )
        assert r.is_fallback is True
        assert r.attempts == 3

    def test_empty(self):
        r = LLMResult(content="")
        assert r.content == ""
        assert r.total_tokens == 0


class TestLLMChunk:
    def test_creation(self):
        c = LLMChunk(content="Hello", model="gpt-4o", provider="openai")
        assert c.content == "Hello"


class TestRoutingContext:
    def test_defaults(self):
        ctx = RoutingContext()
        assert ctx.user_input == ""
        assert ctx.messages == []
        assert ctx.mode_hint is None
        assert ctx.stream is False
        assert ctx.timeout == 120.0

    def test_with_overrides(self):
        ctx = RoutingContext(
            user_input="write code",
            mode_hint="coding",
            temperature=0.2,
            max_tokens=8192,
            model_override="openai:gpt-4o",
        )
        assert ctx.mode_hint == "coding"
        assert ctx.temperature == 0.2
        assert ctx.model_override == "openai:gpt-4o"


# ---- Protocol conformance tests ----


class TestModeRouterProtocol:
    def test_llm_mode_router_conforms(self):
        """LLMModeRouter should conform to ModeRouterProtocol."""
        from navig.llm_router import LLMModeRouter

        router = LLMModeRouter({})
        assert isinstance(router, ModeRouterProtocol)


class TestProviderFactory:
    def test_singleton(self):
        f1 = get_provider_factory()
        f2 = get_provider_factory()
        assert f1 is f2

    def test_create_ollama(self):
        """Ollama provider should be creatable."""
        factory = UnifiedProviderFactory()
        client = factory.get_client("ollama")
        assert isinstance(client, LLMProviderAdapter)

    def test_cache_reuse(self):
        factory = UnifiedProviderFactory()
        c1 = factory.get_client("ollama")
        c2 = factory.get_client("ollama")
        assert c1 is c2


# ---- Provider adapter tests ----


class TestLLMProviderAdapter:
    def test_adapter_wraps_provider(self):
        class FakeProvider:
            async def chat(
                self, model, messages, temperature=0.7, max_tokens=512, **kw
            ):
                class R:
                    content = "test response"
                    model_attr = model
                    provider = "fake"
                    latency_ms = 42
                    prompt_tokens = 5
                    completion_tokens = 10
                    finish_reason = "stop"
                    raw = {}

                r = R()
                r.model = model
                return r

            async def close(self):
                pass

        adapter = LLMProviderAdapter(FakeProvider())
        import asyncio

        result = asyncio.run(
            adapter.complete(
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
            )
        )
        assert isinstance(result, LLMResult)
        assert result.content == "test response"
        assert result.model == "test-model"
        assert result.latency_ms == 42

    def test_stream_not_implemented(self):
        adapter = LLMProviderAdapter(None)
        import asyncio

        with pytest.raises(NotImplementedError):
            asyncio.run(adapter.stream([], "model"))


# ---- Eval hook (router evaluation) ----


class TestRouterEval:
    """Evaluate routing consistency across known test prompts."""

    EVAL_CASES = [
        ("hey", "small_talk"),
        ("Hi there!", "small_talk"),
        ("thanks", "small_talk"),
        ("write a python function to sort a list", "coding"),
        ("debug this error: TypeError", "coding"),
        ("```javascript\nconsole.log('test')\n```", "coding"),
        ("summarize the meeting notes", "summarize"),
        ("tl;dr of this document", "summarize"),
        ("research the latest AI trends", "research"),
        ("compare React vs Vue", "research"),
        ("create a detailed migration plan for the database", "big_tasks"),
    ]

    def test_mode_detection_eval(self):
        from navig.llm_router import detect_mode

        failures = []
        for prompt, expected_mode in self.EVAL_CASES:
            actual = detect_mode(prompt)
            if actual != expected_mode:
                failures.append(f"  {prompt!r}: expected={expected_mode}, got={actual}")
        if failures:
            pytest.fail("Mode detection eval failures:\n" + "\n".join(failures))

    def test_heuristic_tier_eval(self):
        from navig.agent.model_router import ModelSlot, RoutingConfig, heuristic_route

        cfg = RoutingConfig(
            enabled=True,
            mode="rules_then_fallback",
            small=ModelSlot(provider="ollama", model="s", max_tokens=200),
            big=ModelSlot(provider="openrouter", model="b", max_tokens=4096),
            coder_big=ModelSlot(provider="openrouter", model="c", max_tokens=8192),
        )
        TIER_CASES = [
            ("hey how are you?", "small"),
            ("```python\nprint('hello')\n```", "coder_big"),
            ("fix the code bug in auth module", "coder_big"),
            ("design a comprehensive architecture", "big"),
        ]
        failures = []
        for prompt, expected_tier in TIER_CASES:
            actual = heuristic_route(prompt, cfg)
            if actual.tier != expected_tier:
                failures.append(
                    f"  {prompt!r}: expected={expected_tier}, got={actual.tier} ({actual.reason})"
                )
        if failures:
            pytest.fail("Tier routing eval failures:\n" + "\n".join(failures))
