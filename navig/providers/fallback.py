"""
NAVIG AI Providers - Fallback and Retry Mechanism

Implements model fallback with candidate resolution, cooldown tracking,
and retry logic. Based on robust fallback architecture.
"""

import asyncio
import threading
import time
from dataclasses import dataclass
from typing import TypeVar

from .auth import AuthProfileManager
from .clients import (
    BaseProviderClient,
    CompletionRequest,
    CompletionResponse,
    ProviderError,
    create_client,
)
from .types import BUILTIN_PROVIDERS, ProviderConfig

T = TypeVar("T")


@dataclass
class FallbackCandidate:
    """A candidate model for fallback."""

    provider_name: str
    model: str
    priority: int = 0
    config: ProviderConfig | None = None


@dataclass
class FallbackResult:
    """Result of a fallback-enabled operation."""

    response: CompletionResponse
    provider_used: str
    model_used: str
    attempts: int
    candidates_tried: list[str]


@dataclass
class CooldownEntry:
    """Cooldown state for a provider/model."""

    cooldown_until: float
    failure_count: int = 1


class FallbackManager:
    """
    Manages model fallback with cooldown and retry logic.

    Features:
    - Multiple fallback candidates with priority
    - Cooldown for rate-limited/failed providers
    - Exponential backoff
    - Allowlist/blocklist support
    """

    # Cooldown durations in seconds (exponential)
    COOLDOWN_DURATIONS = [
        5 * 60,  # 5 minutes
        15 * 60,  # 15 minutes
        60 * 60,  # 1 hour
        5 * 60 * 60,  # 5 hours
    ]
    MAX_COOLDOWN = 24 * 60 * 60  # 24 hours max

    def __init__(
        self,
        auth_manager: AuthProfileManager | None = None,
        providers: dict[str, ProviderConfig] | None = None,
    ):
        self.auth = auth_manager or AuthProfileManager()
        self.providers = providers or BUILTIN_PROVIDERS.copy()
        self._cooldowns: dict[str, CooldownEntry] = {}
        self._clients: dict[str, BaseProviderClient] = {}

    def add_provider(self, config: ProviderConfig):
        """Add or update a provider configuration."""
        self.providers[config.name.lower()] = config

    def get_provider(self, name: str) -> ProviderConfig | None:
        """Get provider by name."""
        return self.providers.get(name.lower())

    def _get_cooldown_key(self, provider: str, model: str | None = None) -> str:
        """Generate cooldown key for provider/model."""
        if model:
            return f"{provider}:{model}"
        return provider

    def is_in_cooldown(self, provider: str, model: str | None = None) -> bool:
        """Check if provider/model is in cooldown."""
        key = self._get_cooldown_key(provider, model)
        entry = self._cooldowns.get(key)

        if entry is None:
            return False

        if time.time() >= entry.cooldown_until:
            # Cooldown expired
            del self._cooldowns[key]
            return False

        return True

    def get_cooldown_remaining(self, provider: str, model: str | None = None) -> float:
        """Get remaining cooldown time in seconds."""
        key = self._get_cooldown_key(provider, model)
        entry = self._cooldowns.get(key)

        if entry is None:
            return 0

        remaining = entry.cooldown_until - time.time()
        return max(0, remaining)

    def mark_failure(
        self,
        provider: str,
        model: str | None = None,
        error: ProviderError | None = None,
    ):
        """
        Mark a provider/model as failed and apply cooldown.

        Rate limit and billing errors get longer cooldowns.
        """
        key = self._get_cooldown_key(provider, model)
        entry = self._cooldowns.get(key)

        # Determine failure count
        failure_count = (entry.failure_count + 1) if entry else 1

        # Calculate cooldown duration based on failure count
        idx = min(failure_count - 1, len(self.COOLDOWN_DURATIONS) - 1)
        duration = self.COOLDOWN_DURATIONS[idx]

        # Billing errors get max cooldown immediately
        if error and error.error_type == "billing":
            duration = self.MAX_COOLDOWN
        # Rate limit gets doubled cooldown
        elif error and error.error_type == "rate_limit":
            duration = min(duration * 2, self.MAX_COOLDOWN)

        self._cooldowns[key] = CooldownEntry(
            cooldown_until=time.time() + duration,
            failure_count=failure_count,
        )

    def mark_success(self, provider: str, model: str | None = None):
        """Mark provider/model as successful, clearing cooldown."""
        key = self._get_cooldown_key(provider, model)
        if key in self._cooldowns:
            del self._cooldowns[key]

        # Note: Profile success tracking would require tracking which profile was used
        # For now, we skip this as it's optional analytics

    def resolve_candidates(
        self,
        primary_model: str,
        primary_provider: str | None = None,
        fallback_models: list[str] | None = None,
        allowlist: set[str] | None = None,
        blocklist: set[str] | None = None,
    ) -> list[FallbackCandidate]:
        """
        Resolve ordered list of fallback candidates.

        Args:
            primary_model: Primary model to use
            primary_provider: Primary provider (inferred if not specified)
            fallback_models: Additional fallback models (provider:model or model)
            allowlist: Only use these providers (if specified)
            blocklist: Exclude these providers

        Returns:
            Ordered list of fallback candidates
        """
        candidates: list[FallbackCandidate] = []
        seen: set[str] = set()

        def add_candidate(provider: str, model: str, priority: int):
            key = f"{provider}:{model}"
            if key in seen:
                return

            # Check allowlist/blocklist
            if allowlist and provider.lower() not in allowlist:
                return
            if blocklist and provider.lower() in blocklist:
                return

            # Check cooldown
            if self.is_in_cooldown(provider, model):
                return

            seen.add(key)
            config = self.get_provider(provider)
            candidates.append(
                FallbackCandidate(
                    provider_name=provider,
                    model=model,
                    priority=priority,
                    config=config,
                )
            )

        # Parse model string (may include provider prefix)
        def parse_model_spec(spec: str) -> tuple[str | None, str]:
            if ":" in spec and "/" not in spec.split(":")[0]:
                provider, model = spec.split(":", 1)
                return provider, model
            return None, spec

        # Infer provider from model name
        def infer_provider(model: str) -> str | None:
            model_lower = model.lower()
            if model_lower.startswith("gpt-") or model_lower.startswith("o1"):
                return "openai"
            if model_lower.startswith("claude"):
                return "anthropic"
            if "/" in model:
                # OpenRouter-style: provider/model
                return "openrouter"
            for name, config in self.providers.items():
                for m in config.models:
                    if m.id.lower() == model_lower:
                        return name
            return None

        # Add primary
        provider, model = parse_model_spec(primary_model)

        # If model is actually a provider name (no provider prefix), treat it as provider
        if not provider and model.lower() in self.providers:
            provider = model.lower()
            config = self.get_provider(provider)
            if config and config.models:
                model = config.models[0].id

        # Infer provider if still not set
        if not provider:
            provider = primary_provider or infer_provider(model)

        if provider:
            add_candidate(provider, model, priority=0)

        # Add fallbacks
        if fallback_models:
            for i, spec in enumerate(fallback_models):
                provider, model = parse_model_spec(spec)
                if not provider:
                    provider = infer_provider(model)
                if provider:
                    add_candidate(provider, model, priority=i + 1)

        return candidates

    async def get_client(
        self,
        provider: str,
        timeout: float = 60.0,
    ) -> BaseProviderClient:
        """
        Get or create a client for a provider.

        Handles API key resolution automatically.
        """
        key = provider.lower()

        if key in self._clients:
            return self._clients[key]

        config = self.get_provider(key)
        if not config:
            raise ValueError(f"Unknown provider: {provider}")

        # Resolve API key
        api_key, source = self.auth.resolve_auth(key)

        client = create_client(config, api_key=api_key, timeout=timeout)
        self._clients[key] = client
        return client

    async def run_with_fallback(
        self,
        request: CompletionRequest,
        fallback_models: list[str] | None = None,
        allowlist: set[str] | None = None,
        blocklist: set[str] | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
    ) -> FallbackResult:
        """
        Execute a completion request with automatic fallback.

        Args:
            request: The completion request
            fallback_models: Additional models to try on failure
            allowlist: Only use these providers
            blocklist: Exclude these providers
            max_retries: Max retries per candidate
            retry_delay: Base delay between retries (exponential)

        Returns:
            FallbackResult with response and metadata

        Raises:
            ProviderError: If all candidates fail
        """
        candidates = self.resolve_candidates(
            primary_model=request.model,
            fallback_models=fallback_models,
            allowlist=allowlist,
            blocklist=blocklist,
        )

        if not candidates:
            raise ProviderError(
                message="No available candidates (all in cooldown or filtered)",
                error_type="no_candidates",
            )

        candidates_tried: list[str] = []
        last_error: ProviderError | None = None
        total_attempts = 0

        for candidate in candidates:
            candidate_key = f"{candidate.provider_name}:{candidate.model}"
            candidates_tried.append(candidate_key)

            # Get client for this provider
            try:
                client = await self.get_client(candidate.provider_name)
            except (ValueError, ProviderError) as e:
                last_error = ProviderError(
                    message=str(e),
                    provider=candidate.provider_name,
                )
                continue

            # Create request with this model
            req = CompletionRequest(
                messages=request.messages,
                model=candidate.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
                tools=request.tools,
                tool_choice=request.tool_choice,
                stream=request.stream,
                stop=request.stop,
            )

            # Retry loop for this candidate
            for attempt in range(max_retries):
                total_attempts += 1
                try:
                    response = await client.complete(req)

                    # Success!
                    self.mark_success(candidate.provider_name, candidate.model)

                    return FallbackResult(
                        response=response,
                        provider_used=candidate.provider_name,
                        model_used=candidate.model,
                        attempts=total_attempts,
                        candidates_tried=candidates_tried,
                    )

                except ProviderError as e:
                    last_error = e

                    # Non-retryable errors: move to next candidate
                    if not e.retryable:
                        self.mark_failure(candidate.provider_name, candidate.model, e)
                        break

                    # Retryable: wait and retry
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay * (2**attempt))
                    else:
                        # Max retries reached
                        self.mark_failure(candidate.provider_name, candidate.model, e)

        # All candidates failed
        raise last_error or ProviderError(
            message="All fallback candidates failed",
            error_type="fallback_exhausted",
        )

    async def close(self):
        """Close all clients."""
        for client in self._clients.values():
            await client.close()
        self._clients.clear()


# Global instance
_fallback_manager: FallbackManager | None = None
_fallback_manager_lock = threading.Lock()


def get_fallback_manager() -> FallbackManager:
    """Get or create global fallback manager."""
    global _fallback_manager
    if _fallback_manager is None:
        with _fallback_manager_lock:
            if _fallback_manager is None:
                _fallback_manager = FallbackManager()
    return _fallback_manager


async def complete_with_fallback(
    messages: list[dict[str, str]],
    model: str = "gpt-4o-mini",
    fallback_models: list[str] | None = None,
    temperature: float = 0.7,
    max_tokens: int = 4096,
    tools: list[dict] | None = None,
) -> FallbackResult:
    """
    Convenience function for completion with fallback.

    Args:
        messages: Chat messages as dicts
        model: Primary model
        fallback_models: Fallback models
        temperature: Sampling temperature
        max_tokens: Max response tokens
        tools: Optional tool definitions

    Returns:
        FallbackResult
    """
    from .clients import Message, ToolDefinition

    manager = get_fallback_manager()

    # Convert messages
    msg_list = [Message(role=m["role"], content=m["content"]) for m in messages]

    # Convert tools
    tool_list = None
    if tools:
        tool_list = [
            ToolDefinition(
                name=t.get("name", ""),
                description=t.get("description", ""),
                parameters=t.get("parameters", {}),
            )
            for t in tools
        ]

    request = CompletionRequest(
        messages=msg_list,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        tools=tool_list,
    )

    return await manager.run_with_fallback(
        request,
        fallback_models=fallback_models,
    )
