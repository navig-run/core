"""
Unified LLM Router — canonical routing for all NAVIG entrypoints.

Replaces the split-brain between llm_router.py (5-mode) and
model_router.py (3-tier) with a single router that:

1. Classifies the task (mode + confidence)
2. Checks providers in priority order (VS Code Copilot first)
3. For VS Code providers: passes purpose, lets VS Code pick model
4. For fallback providers: selects model by mode + capabilities
5. Executes the request
6. Post-response audit (retry/escalate on failure)
7. Logs full route trace in JSONL

Provider priority:
    mcp_forge → openrouter → github_models → ollama → error
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from navig.routing.capabilities import (
    MODE_CAPABILITIES,
    MODE_MODEL_PREFERENCE,
    ModeProfile,
)
from navig.routing.detect import detect_mode
from navig.routing.trace import RouteTrace, log_trace

logger = logging.getLogger(__name__)

# ── Confidence threshold for mini-classify / fail-up ────────────────

CONFIDENCE_THRESHOLD = 0.6

# ── Low-quality response patterns (from model_router.py) ───────────

_LOW_QUALITY = re.compile(
    r"(i'?m not sure|i cannot|i can'?t help|i don'?t know|"
    r"as an ai|i'?m (just )?an? ai|"
    r"i apologize|sorry.{0,30}(can'?t|cannot|unable))",
    re.IGNORECASE,
)

_HEDGE = re.compile(
    r"((?:it'?s? )?(?:possible|might|maybe|perhaps|could be)(?:\s+that)?)",
    re.IGNORECASE,
)


# ── Route Request / Decision ────────────────────────────────────────

class RouteRequest:
    """Input to the unified router."""

    __slots__ = (
        "messages", "text", "temperature", "max_tokens",
        "tier_override", "model_override", "entrypoint", "metadata",
    )

    def __init__(
        self,
        messages: List[Dict[str, str]],
        text: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        tier_override: str = "",
        model_override: str = "",
        entrypoint: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.messages = messages
        self.text = text or _extract_user_text(messages)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.tier_override = tier_override
        self.model_override = model_override
        self.entrypoint = entrypoint
        self.metadata = metadata or {}


class RouteDecision:
    """Output of the route() step — everything needed before execution."""

    __slots__ = (
        "mode", "confidence", "reasons", "provider", "model",
        "purpose", "max_tokens", "temperature", "capabilities",
    )

    def __init__(
        self,
        mode: str = "big_tasks",
        confidence: float = 0.5,
        reasons: Optional[List[str]] = None,
        provider: str = "",
        model: str = "",
        purpose: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        capabilities: Optional[ModeProfile] = None,
    ):
        self.mode = mode
        self.confidence = confidence
        self.reasons = reasons or []
        self.provider = provider
        self.model = model
        self.purpose = purpose
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.capabilities = capabilities


# ── Provider Status ─────────────────────────────────────────────────

class ProviderInfo:
    """Runtime information about a provider."""

    def __init__(self, name: str, available: bool = False, models: Optional[List[str]] = None,
                 capabilities: Optional[Dict[str, List[str]]] = None, error: str = ""):
        self.name = name
        self.available = available
        self.models = models or []
        self.capabilities = capabilities or {}
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "models": self.models,
            "capabilities": self.capabilities,
            "error": self.error,
        }


class ProviderStatus:
    """Snapshot of all provider availability."""

    def __init__(self, providers: Optional[List[ProviderInfo]] = None,
                 active_provider: str = "", router_mode: str = "unified"):
        self.providers = providers or []
        self.active_provider = active_provider
        self.router_mode = router_mode
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_provider": self.active_provider,
            "router_mode": self.router_mode,
            "timestamp": self.timestamp,
            "providers": [p.to_dict() for p in self.providers],
        }


# ── Unified Router ──────────────────────────────────────────────────

class UnifiedRouter:
    """
    Single canonical LLM router for all NAVIG entrypoints.

    Usage:
        router = UnifiedRouter(config)
        response_text = await router.run(request)
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._config = config or {}
        self._providers: Dict[str, Any] = {}  # name → LLMProvider instance
        self._provider_available: Dict[str, bool] = {}
        self._last_health_check: float = 0.0
        self._health_cache_ttl = 30.0  # seconds
        self._active_provider: str = ""

    # ── Public API ──────────────────────────────────────────────────

    def route(self, request: RouteRequest) -> RouteDecision:
        """
        Synchronous routing decision (no provider health checks).
        Classifies the task and determines the target mode + capabilities.
        """
        # Tier override → direct mapping
        if request.tier_override:
            tier_to_mode = {
                "small": "small_talk", "big": "big_tasks",
                "coder_big": "coding", "coder": "coding",
            }
            mode = tier_to_mode.get(request.tier_override, "big_tasks")
            return RouteDecision(
                mode=mode, confidence=1.0,
                reasons=[f"tier_override:{request.tier_override}"],
                purpose=mode, max_tokens=request.max_tokens,
                temperature=request.temperature,
                capabilities=MODE_CAPABILITIES.get(mode),
            )

        # Model override → big_tasks by default
        if request.model_override:
            return RouteDecision(
                mode="big_tasks", confidence=1.0,
                reasons=["model_override"], model=request.model_override,
                purpose="big_tasks", max_tokens=request.max_tokens,
                temperature=request.temperature,
                capabilities=MODE_CAPABILITIES.get("big_tasks"),
            )

        # Detect mode
        mode, confidence, reasons = detect_mode(request.text)

        # Confidence gate: fail up to big_tasks
        if confidence < CONFIDENCE_THRESHOLD:
            reasons.append(f"low_confidence({confidence:.2f})→failup")
            mode = "big_tasks"

        caps = MODE_CAPABILITIES.get(mode)
        return RouteDecision(
            mode=mode, confidence=confidence, reasons=reasons,
            purpose=mode, max_tokens=request.max_tokens,
            temperature=request.temperature, capabilities=caps,
        )

    async def run(self, request: RouteRequest) -> Tuple[str, RouteTrace]:
        """
        Route + execute + audit + retry.

        Returns (response_text, trace).
        """
        trace = RouteTrace(
            trace_id=uuid.uuid4().hex[:12],
            timestamp=time.time(),
            entrypoint=request.entrypoint,
        )
        t0 = time.monotonic()

        # 1. Classify
        decision = self.route(request)
        trace.mode = decision.mode
        trace.confidence = decision.confidence
        trace.reasons = decision.reasons
        trace.capability_profile = decision.mode
        trace.purpose_sent = decision.purpose

        # 2. Try providers in priority order
        provider_chain = self._get_provider_chain()
        response_text = ""
        last_error = ""

        for provider_name in provider_chain:
            provider = self._get_provider_instance(provider_name)
            if provider is None:
                continue

            # Health check
            try:
                available = await provider.is_available()
            except Exception:
                available = False

            if not available:
                trace.fallbacks_tried.append(provider_name)
                continue

            # Execute
            try:
                response_text = await self._execute(
                    provider, provider_name, decision, request,
                )
                trace.provider = provider_name
                trace.model = decision.model or "(purpose-selected)"
                self._active_provider = provider_name
                break
            except Exception as e:
                last_error = str(e)
                trace.fallbacks_tried.append(provider_name)
                logger.warning(
                    "Provider %s failed for mode=%s: %s",
                    provider_name, decision.mode, e,
                )
                continue

        if not response_text and not trace.provider:
            trace.audit_result = "failed"
            trace.latency_ms = int((time.monotonic() - t0) * 1000)
            log_trace(trace)
            raise RuntimeError(
                f"No provider available for mode={decision.mode}. "
                f"Tried: {trace.fallbacks_tried}. Last error: {last_error}"
            )

        # 3. Post-response audit
        audit = self._audit_response(response_text, decision)
        trace.audit_result = audit

        if audit == "retry" and trace.provider != "mcp_forge":
            # Escalate: try next provider with stronger model
            escalated = await self._escalate(
                request, decision, trace, provider_chain, t0,
            )
            if escalated:
                response_text = escalated

        trace.latency_ms = int((time.monotonic() - t0) * 1000)
        log_trace(trace)

        logger.info(
            "Route: mode=%s conf=%.2f provider=%s model=%s latency=%dms audit=%s fallbacks=%s",
            trace.mode, trace.confidence, trace.provider,
            trace.model, trace.latency_ms, trace.audit_result,
            trace.fallbacks_tried,
        )

        return response_text, trace

    async def status(self) -> ProviderStatus:
        """Check all provider availability and return status snapshot."""
        providers: List[ProviderInfo] = []

        for name in self._get_provider_chain():
            provider = self._get_provider_instance(name)
            if provider is None:
                providers.append(ProviderInfo(name=name, error="not_configured"))
                continue
            try:
                available = await provider.is_available()
                providers.append(ProviderInfo(
                    name=name, available=available,
                ))
            except Exception as e:
                providers.append(ProviderInfo(name=name, error=str(e)))

        return ProviderStatus(
            providers=providers,
            active_provider=self._active_provider,
        )

    # ── Provider Management ─────────────────────────────────────────

    def _get_provider_chain(self) -> List[str]:
        """Ordered provider chain. VS Code first, then cloud, then local."""
        chain = self._config.get("provider_chain", [
            "mcp_forge", "openrouter", "github_models", "ollama",
        ])
        return chain

    def _get_provider_instance(self, name: str):
        """Get or create a provider instance by name."""
        if name in self._providers:
            return self._providers[name]

        try:
            instance = self._create_provider(name)
            if instance:
                self._providers[name] = instance
            return instance
        except Exception as e:
            logger.debug("Cannot create provider %s: %s", name, e)
            return None

    def _create_provider(self, name: str):
        """Create a provider from config."""
        from navig.agent.llm_providers import (
            McpForgeProvider, OpenRouterProvider,
            GitHubModelsProvider, OllamaProvider,
        )

        forge_cfg = self._config.get("forge", {})
        forge_token = forge_cfg.get("token", "")

        if name == "mcp_forge":
            mcp_url = forge_cfg.get("mcp_url", "")
            if not mcp_url:
                return None
            return McpForgeProvider(base_url=mcp_url, api_key=forge_token)

        elif name == "openrouter":
            import os
            api_key = self._config.get("openrouter_api_key", "") or os.getenv("OPENROUTER_API_KEY", "")
            if not api_key:
                # Try vault
                try:
                    from navig.vault import get_vault
                    vault = get_vault()
                    secret = vault.get_secret("openrouter", "api_key", caller="unified_router")
                    if secret:
                        api_key = secret.reveal().strip()
                except Exception:
                    pass
            if not api_key:
                return None
            return OpenRouterProvider(api_key=api_key)

        elif name == "github_models":
            import os
            token = os.getenv("GITHUB_TOKEN", "")
            if not token:
                try:
                    from navig.agent.llm_providers import GitHubModelsProvider as GMP
                    token = GMP._resolve_token(GMP)
                except Exception:
                    pass
            if not token:
                return None
            return GitHubModelsProvider(api_key=token)

        elif name == "ollama":
            return OllamaProvider()

        return None

    # ── Execution ───────────────────────────────────────────────────

    async def _execute(
        self, provider, provider_name: str,
        decision: RouteDecision, request: RouteRequest,
    ) -> str:
        """Execute a request against a specific provider."""

        # VS Code providers: pass purpose, let VS Code pick model
        if provider_name == "mcp_forge":
            kwargs: Dict[str, Any] = {}
            if provider_name == "mcp_forge":
                kwargs["purpose"] = decision.purpose
            resp = await provider.chat(
                model=decision.model or "",
                messages=request.messages,
                temperature=decision.temperature,
                max_tokens=decision.max_tokens,
                **kwargs,
            )
            if hasattr(resp, "content"):
                decision.model = getattr(resp, "model", decision.model)
                return resp.content
            return str(resp)

        # Fallback providers: select model by mode preference
        model = decision.model
        if not model:
            prefs = MODE_MODEL_PREFERENCE.get(decision.mode, {})
            model = prefs.get(provider_name, "")

        if not model:
            raise RuntimeError(f"No model configured for {provider_name}/{decision.mode}")

        resp = await provider.chat(
            model=model,
            messages=request.messages,
            temperature=decision.temperature,
            max_tokens=decision.max_tokens,
        )
        if hasattr(resp, "content"):
            decision.model = getattr(resp, "model", model)
            return resp.content
        return str(resp)

    # ── Audit & Escalation ──────────────────────────────────────────

    def _audit_response(self, text: str, decision: RouteDecision) -> str:
        """
        Post-response quality audit.
        Returns "pass", "retry", or "accept_low".
        """
        if not text or len(text.strip()) < 10:
            return "retry"

        # Only audit non-small_talk modes
        if decision.mode == "small_talk":
            return "pass"

        if _LOW_QUALITY.search(text[:500]):
            return "retry"

        if decision.mode in ("coding", "big_tasks") and _HEDGE.search(text[:300]):
            return "retry"

        return "pass"

    async def _escalate(
        self, request: RouteRequest, decision: RouteDecision,
        trace: RouteTrace, provider_chain: List[str], t0: float,
    ) -> Optional[str]:
        """
        Try the next available stronger provider (max 1 escalation).
        """
        # Find the index of current provider in chain
        current_idx = -1
        for i, name in enumerate(provider_chain):
            if name == trace.provider:
                current_idx = i
                break

        if current_idx < 0:
            return None

        # Try the next providers (skip current)
        for name in provider_chain[current_idx + 1:]:
            provider = self._get_provider_instance(name)
            if provider is None:
                continue
            try:
                available = await provider.is_available()
            except Exception:
                available = False
            if not available:
                continue

            try:
                text = await self._execute(provider, name, decision, request)
                trace.provider = name
                trace.audit_result = "retry_1"
                logger.info("Escalated to %s after audit failure", name)
                return text
            except Exception:
                continue

        return None

    # ── Cleanup ─────────────────────────────────────────────────────

    async def close(self):
        """Close all provider sessions."""
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception:
                pass
        self._providers.clear()


# ── Singleton ───────────────────────────────────────────────────────

_router: Optional[UnifiedRouter] = None


def get_router(config: Optional[Dict[str, Any]] = None) -> UnifiedRouter:
    """Get or create the singleton unified router."""
    global _router
    if _router is None:
        if config is None:
            try:
                from navig.config import get_config_manager
                config = get_config_manager().global_config
            except Exception:
                config = {}
        _router = UnifiedRouter(config)
    return _router


def reset_router() -> None:
    """Reset the singleton (for testing or config reload)."""
    global _router
    if _router:
        # Don't await close in sync context
        pass
    _router = None


def _extract_user_text(messages: List[Dict[str, str]]) -> str:
    """Extract the last user message text."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    if messages:
        return messages[-1].get("content", "")
    return ""
