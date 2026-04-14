"""
LLM Routing Types - Explicit Protocol & ABC interfaces.

Defines the contracts for the three routing layers:
  Layer 1: ModeRouter - classifies user input -> canonical mode
  Layer 2: ModelRouter - maps mode -> concrete provider+model+params
  Layer 3: LLMClient - transports a prompt to a provider and returns a response

All existing routers and providers conform to these protocols without
requiring inheritance, but new implementations SHOULD inherit for IDE support.

Usage:
    from navig.llm_routing_types import ModeRouterProtocol, ModelRouterProtocol, LLMClientProtocol
"""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE

logger = logging.getLogger("navig.llm_routing_types")


# ---- Shared data structures ----


@dataclass
class ModelSelection:
    """Output of model routing - everything needed to dispatch an LLM call."""

    provider_name: str
    model_name: str
    temperature: float = _DEFAULT_TEMPERATURE
    max_tokens: int = _DEFAULT_MAX_TOKENS
    base_url: str = ""
    api_key_env: str = ""
    tier: str = ""
    strategy_name: str = ""
    is_uncensored: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"ModelSelection({self.provider_name}:{self.model_name}, "
            f"tier={self.tier}, strategy={self.strategy_name})"
        )


@dataclass
class LLMResult:
    """Unified response from any LLM provider."""

    content: str
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    is_fallback: bool = False
    attempts: int = 1
    selection: ModelSelection | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class LLMChunk:
    """A single chunk from a streaming LLM response."""

    content: str
    model: str = ""
    provider: str = ""
    finish_reason: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingContext:
    """Context passed through the routing pipeline."""

    user_input: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    mode_hint: str | None = None
    tier_override: str | None = None
    model_override: str | None = None
    provider_override: str | None = None
    prefer_uncensored: bool | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    timeout: float = 120.0
    metadata: dict[str, Any] = field(default_factory=dict)


# ---- Layer 1 Protocol: Mode Router ----


@runtime_checkable
class ModeRouterProtocol(Protocol):
    """
    Classifies user input into a canonical LLM mode.
    Canonical modes: small_talk, big_tasks, coding, summarize, research.
    Implementations: navig.llm_router.LLMModeRouter
    """

    def resolve_mode(self, hint: str) -> str:
        """Resolve a mode hint/alias to a canonical mode name."""
        ...

    def detect_mode(self, user_input: str) -> str:
        """Auto-detect mode from raw user text. Returns canonical mode name."""
        ...


# ---- Layer 2 Protocol: Model Router ----


@runtime_checkable
class ModelRouterProtocol(Protocol):
    """
    Maps a canonical mode to a concrete provider + model + params.
    Implementations:
      - navig.llm_router.LLMModeRouter.get_config()
      - navig.agent.model_router.HybridRouter.route()
    """

    def select_model(self, mode: str, context: RoutingContext) -> ModelSelection:
        """Select a concrete model for the given mode and context."""
        ...


# ---- Layer 3 Protocol: LLM Client ----


@runtime_checkable
class LLMClientProtocol(Protocol):
    """
    Sends a prompt to an LLM provider and returns a response.
    Implementations:
      - navig.agent.llm_providers.LLMProvider  (aiohttp)
      - navig.providers.clients.BaseProviderClient  (httpx)
    """

    async def complete(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        **kwargs: Any,
    ) -> LLMResult:
        """Send messages to provider and return result."""
        ...

    async def stream(
        self,
        messages: list[dict[str, str]],
        model: str,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        **kwargs: Any,
    ) -> AsyncIterator[LLMChunk]:
        """Stream responses from the provider, yields LLMChunk objects."""
        ...


# ---- Provider Factory Protocol ----


@runtime_checkable
class ProviderFactoryProtocol(Protocol):
    """Creates LLM client instances by provider name."""

    def get_client(self, provider_name: str, **kwargs: Any) -> LLMClientProtocol:
        """Get or create a client for the named provider."""
        ...


# ---- Adapter: LLMProvider (aiohttp) -> LLMClientProtocol ----


class LLMProviderAdapter:
    """Wraps navig.agent.llm_providers.LLMProvider to LLMClientProtocol."""

    def __init__(self, provider):
        self._provider = provider

    async def complete(self, messages, model, temperature=_DEFAULT_TEMPERATURE, max_tokens=_DEFAULT_MAX_TOKENS, **kwargs):
        resp = await self._provider.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        return LLMResult(
            content=resp.content,
            model=resp.model,
            provider=resp.provider,
            latency_ms=resp.latency_ms,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            finish_reason=resp.finish_reason,
            raw=resp.raw,
        )

    async def stream(self, messages, model, temperature=_DEFAULT_TEMPERATURE, max_tokens=_DEFAULT_MAX_TOKENS, **kwargs):
        raise NotImplementedError(
            "Streaming is not available for this provider adapter yet. "
            "Use non-streaming completion or switch to a provider client with stream support."
        )

    async def close(self):
        await self._provider.close()


# ---- Adapter: BaseProviderClient (httpx) -> LLMClientProtocol ----


class ProviderClientAdapter:
    """Wraps navig.providers.clients.BaseProviderClient to LLMClientProtocol."""

    def __init__(self, client):
        self._client = client

    async def complete(self, messages, model, temperature=_DEFAULT_TEMPERATURE, max_tokens=_DEFAULT_MAX_TOKENS, **kwargs):
        from navig.providers.clients import CompletionRequest, Message

        msgs = [Message(role=m["role"], content=m["content"]) for m in messages]
        request = CompletionRequest(
            messages=msgs,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        resp = await self._client.complete(request)
        return LLMResult(
            content=resp.content or "",
            model=resp.model or model,
            provider=getattr(self._client, "name", "unknown"),
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            finish_reason=resp.finish_reason or "",
        )

    async def stream(self, messages, model, temperature=_DEFAULT_TEMPERATURE, max_tokens=_DEFAULT_MAX_TOKENS, **kwargs):
        raise NotImplementedError(
            "Streaming is not available for this provider client adapter yet. "
            "Use non-streaming completion or integrate a streaming-capable adapter."
        )

    async def close(self):
        await self._client.close()


# ---- Unified provider factory ----


class UnifiedProviderFactory:
    """
    Creates LLMClientProtocol-conforming clients from either provider stack.
    Tries agent/llm_providers first (aiohttp), then providers/clients (httpx).
    """

    def __init__(self):
        self._cache: dict[str, Any] = {}

    def get_client(self, provider_name: str, **kwargs: Any) -> Any:
        cache_key = f"{provider_name}:{kwargs.get('base_url', 'default')}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        client = self._create_client(provider_name, **kwargs)
        self._cache[cache_key] = client
        return client

    def _create_client(self, provider_name: str, **kwargs: Any) -> Any:
        try:
            from navig.agent.llm_providers import _PROVIDER_MAP, create_provider

            if provider_name.lower() in _PROVIDER_MAP:
                provider = create_provider(provider_name, **kwargs)
                return LLMProviderAdapter(provider)
        except (ImportError, ValueError):
            pass  # optional provider not available or invalid config; skip
        try:
            from navig.providers.auth import resolve_auth
            from navig.providers.clients import create_client, get_builtin_provider

            config = get_builtin_provider(provider_name)
            if config is None:
                from navig.llm_router import PROVIDER_BASE_URLS
                from navig.providers.types import ModelApi, ProviderConfig

                url = kwargs.get("base_url") or PROVIDER_BASE_URLS.get(
                    provider_name, "https://openrouter.ai/api/v1"
                )
                config = ProviderConfig(
                    name=provider_name, base_url=url, api=ModelApi.OPENAI_COMPLETIONS
                )
            api_key = kwargs.get("api_key", "")
            if not api_key:
                api_key, _ = resolve_auth(provider_name)
            client = create_client(config, api_key=api_key, timeout=kwargs.get("timeout", 120.0))
            return ProviderClientAdapter(client)
        except (ImportError, ValueError) as e:
            raise ValueError(f"Cannot create client for provider {provider_name!r}: {e}") from e

    async def close_all(self):
        for client in self._cache.values():
            try:
                await client.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        self._cache.clear()


# Singleton
_factory: UnifiedProviderFactory | None = None
_factory_lock = threading.Lock()


def get_provider_factory() -> UnifiedProviderFactory:
    """Get the global UnifiedProviderFactory singleton."""
    global _factory
    if _factory is None:
        with _factory_lock:
            if _factory is None:
                _factory = UnifiedProviderFactory()
    return _factory
