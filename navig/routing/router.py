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
    mcp_bridge → openrouter → github_models → ollama → error
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any

from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE
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
        "messages",
        "text",
        "temperature",
        "max_tokens",
        "tier_override",
        "model_override",
        "entrypoint",
        "metadata",
    )

    def __init__(
        self,
        messages: list[dict[str, str]],
        text: str = "",
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        tier_override: str = "",
        model_override: str = "",
        entrypoint: str = "",
        metadata: dict[str, Any] | None = None,
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
        "mode",
        "confidence",
        "reasons",
        "provider",
        "model",
        "purpose",
        "max_tokens",
        "temperature",
        "capabilities",
    )

    def __init__(
        self,
        mode: str = "big_tasks",
        confidence: float = 0.5,
        reasons: list[str] | None = None,
        provider: str = "",
        model: str = "",
        purpose: str = "",
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        temperature: float = _DEFAULT_TEMPERATURE,
        capabilities: ModeProfile | None = None,
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

    def __init__(
        self,
        name: str,
        available: bool = False,
        models: list[str] | None = None,
        capabilities: dict[str, list[str]] | None = None,
        error: str = "",
    ):
        self.name = name
        self.available = available
        self.models = models or []
        self.capabilities = capabilities or {}
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "available": self.available,
            "models": self.models,
            "capabilities": self.capabilities,
            "error": self.error,
        }


class ProviderStatus:
    """Snapshot of all provider availability."""

    def __init__(
        self,
        providers: list[ProviderInfo] | None = None,
        active_provider: str = "",
        router_mode: str = "unified",
    ):
        self.providers = providers or []
        self.active_provider = active_provider
        self.router_mode = router_mode
        self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "active_provider": self.active_provider,
            "router_mode": self.router_mode,
            "timestamp": self.timestamp,
            "providers": [p.to_dict() for p in self.providers],
        }


# ── Unified Router ──────────────────────────────────────────────────

# Maps conversation mode names to tier keys used in session_tier_overrides.
_MODE_TO_TIER: dict[str, str] = {
    "small_talk": "small",
    "big_tasks": "big",
    "coding": "coder_big",
    "summarize": "big",
    "research": "big",
}

# Emergency fallback models used when no model is resolved from config or from the
# static MODE_MODEL_PREFERENCE table.  The "_default" key is the catch-all for any
# mode not explicitly listed.  These are conservative, widely-available choices and
# will only fire if a provider was activated without any model being stored.
_PROVIDER_DEFAULT_MODELS: dict[str, dict[str, str]] = {
    "nvidia": {
        "small_talk": "meta/llama-3.1-8b-instruct",
        "big_tasks": "meta/llama-3.1-70b-instruct",
        "coding": "meta/llama-3.1-70b-instruct",
        "summarize": "meta/llama-3.1-70b-instruct",
        "research": "meta/llama-3.1-70b-instruct",
        "_default": "meta/llama-3.1-8b-instruct",
    },
    "xai": {"_default": "grok-2-latest"},
    "anthropic": {"_default": "claude-3-5-haiku-20241022"},
    "google": {"_default": "gemini-1.5-flash"},
    "groq": {"_default": "llama-3.1-70b-versatile"},
    "mistral": {"_default": "mistral-small-latest"},
    "cerebras": {"_default": "llama3.1-8b"},
    "openai": {
        "small_talk": "gpt-4o-mini",
        "big_tasks": "gpt-4o",
        "coding": "gpt-4o",
        "_default": "gpt-4o-mini",
    },
}


class UnifiedRouter:
    """
    Single canonical LLM router for all NAVIG entrypoints.

    Usage:
        router = UnifiedRouter(config)
        response_text = await router.run(request)
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._providers: dict[str, Any] = {}  # name → LLMProvider instance
        self._provider_available: dict[str, bool] = {}
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
                "small": "small_talk",
                "big": "big_tasks",
                "coder_big": "coding",
                "coder": "coding",
            }
            mode = tier_to_mode.get(request.tier_override, "big_tasks")
            return RouteDecision(
                mode=mode,
                confidence=1.0,
                reasons=[f"tier_override:{request.tier_override}"],
                purpose=mode,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                capabilities=MODE_CAPABILITIES.get(mode),
            )

        # Model override → big_tasks by default
        if request.model_override:
            return RouteDecision(
                mode="big_tasks",
                confidence=1.0,
                reasons=["model_override"],
                model=request.model_override,
                purpose="big_tasks",
                max_tokens=request.max_tokens,
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
            mode=mode,
            confidence=confidence,
            reasons=reasons,
            purpose=mode,
            max_tokens=request.max_tokens,
            temperature=request.temperature,
            capabilities=caps,
        )

    async def run(self, request: RouteRequest) -> tuple[str, RouteTrace]:
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

        # 1b. Apply session tier overrides (from Telegram /provider_hybrid).
        #     These override the provider+model for a specific tier/mode
        #     without touching durable config.
        _sto = (request.metadata or {}).get("session_tier_overrides")
        if _sto and isinstance(_sto, dict):
            _tier = _MODE_TO_TIER.get(decision.mode, "big")
            _tier_data = _sto.get(_tier)
            if _tier_data and isinstance(_tier_data, dict):
                _so_model = _tier_data.get("model", "")
                _so_provider = _tier_data.get("provider", "")
                if _so_model:
                    decision.model = _so_model
                    decision.reasons.append(f"session_override:{_tier}")
                    logger.info(
                        "Session override applied: tier=%s provider=%s model=%s",
                        _tier,
                        _so_provider,
                        _so_model,
                    )

        # 2. Try providers in priority order
        provider_chain = self._get_provider_chain()

        # Prioritize session-override provider so we don't waste attempts
        # on providers that don't know the overridden model.
        if _sto and isinstance(_sto, dict):
            _tier_data2 = _sto.get(_MODE_TO_TIER.get(decision.mode, "big"))
            if _tier_data2 and _tier_data2.get("provider"):
                _prov = _tier_data2["provider"]
                if _prov in provider_chain:
                    provider_chain = [_prov] + [p for p in provider_chain if p != _prov]
                else:
                    provider_chain = [_prov] + provider_chain

        # 1c. Apply durable llm_modes config (written by Telegram /models activation).
        #     When the user activates a provider in the Telegram UI, it stores
        #     provider+model in config["llm_router"]["llm_modes"][mode_name].
        #     Without this step the generic provider path in _execute() raises
        #     "No model configured for nvidia/small_talk" because the static
        #     MODE_MODEL_PREFERENCE table only knows the built-in providers.
        #     The provider is promoted to front-of-chain even when the stored
        #     model is empty — _execute() has a per-provider emergency default
        #     table that covers that case.
        if not decision.model:
            try:
                _modes_cfg = (self._config.get("llm_router") or {}).get("llm_modes") or {}
                _mode_data = _modes_cfg.get(decision.mode) or {}
                if isinstance(_mode_data, dict):
                    _cfg_model = _mode_data.get("model", "")
                    _cfg_provider = _mode_data.get("provider", "")
                    if _cfg_provider:
                        # Always promote the provider — even when model is empty.
                        # _execute() will resolve a default model for the provider.
                        if _cfg_provider in provider_chain:
                            provider_chain = [_cfg_provider] + [
                                p for p in provider_chain if p != _cfg_provider
                            ]
                        else:
                            provider_chain = [_cfg_provider] + provider_chain
                        decision.provider = _cfg_provider
                        if _cfg_model:
                            decision.model = _cfg_model
                        decision.reasons.append(f"llm_modes_config:{decision.mode}")
                        logger.info(
                            "llm_modes_config applied: mode=%s provider=%s model=%s",
                            decision.mode,
                            _cfg_provider,
                            _cfg_model or "(provider-default)",
                        )
            except Exception:  # noqa: BLE001
                pass  # best-effort; never block routing

        response_text = ""
        last_error = ""

        for provider_name in provider_chain:
            provider = self._get_provider_instance(provider_name)
            if provider is None:
                trace.fallbacks_tried.append(provider_name)
                logger.debug("Provider %s: no instance (key missing or config error)", provider_name)
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
                    provider,
                    provider_name,
                    decision,
                    request,
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
                    provider_name,
                    decision.mode,
                    e,
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

        if audit == "retry" and trace.provider != "mcp_bridge":
            # Escalate: try next provider with stronger model
            escalated = await self._escalate(
                request,
                decision,
                trace,
                provider_chain,
                t0,
            )
            if escalated:
                response_text = escalated

        trace.latency_ms = int((time.monotonic() - t0) * 1000)
        log_trace(trace)

        logger.info(
            "Route: mode=%s conf=%.2f provider=%s model=%s latency=%dms audit=%s fallbacks=%s",
            trace.mode,
            trace.confidence,
            trace.provider,
            trace.model,
            trace.latency_ms,
            trace.audit_result,
            trace.fallbacks_tried,
        )

        return response_text, trace

    async def status(self) -> ProviderStatus:
        """Check all provider availability and return status snapshot."""
        providers: list[ProviderInfo] = []

        for name in self._get_provider_chain():
            provider = self._get_provider_instance(name)
            if provider is None:
                providers.append(ProviderInfo(name=name, error="not_configured"))
                continue
            try:
                available = await provider.is_available()
                providers.append(
                    ProviderInfo(
                        name=name,
                        available=available,
                    )
                )
            except Exception as e:
                providers.append(ProviderInfo(name=name, error=str(e)))

        return ProviderStatus(
            providers=providers,
            active_provider=self._active_provider,
        )

    # ── Provider Management ─────────────────────────────────────────

    def _get_provider_chain(self) -> list[str]:
        """Ordered provider chain.

        Starts with any user-configured provider (from Telegram /models,
        ``ai.default_provider``, or ``llm_router.llm_modes``), then the
        static defaults: mcp_bridge → openrouter → github_models → ollama.
        """
        static_chain: list[str] = self._config.get(
            "provider_chain",
            [
                "mcp_bridge",
                "openrouter",
                "github_models",
                "ollama",
            ],
        )

        # Discover user-configured providers and prepend them
        user_providers = self._discover_user_providers()
        if not user_providers:
            return static_chain

        chain: list[str] = []
        seen: set[str] = set()
        for p in user_providers + static_chain:
            if p not in seen:
                chain.append(p)
                seen.add(p)
        return chain

    def _discover_user_providers(self) -> list[str]:
        """Read user-configured providers from config (llm_modes, ai.default_provider)."""
        providers: list[str] = []
        seen: set[str] = set()

        def _add(name: str) -> None:
            n = (name or "").strip().lower()
            if n and n not in seen:
                providers.append(n)
                seen.add(n)

        # 1. ai.default_provider (set by Telegram /models activation)
        ai_cfg = self._config.get("ai") or {}
        _add(ai_cfg.get("default_provider", ""))

        # 2. llm_router.llm_modes — unique providers across all modes
        try:
            llm_router_cfg = self._config.get("llm_router") or {}
            modes_cfg = llm_router_cfg.get("llm_modes") or {}
            for _mode_name, mode_data in modes_cfg.items():
                if isinstance(mode_data, dict):
                    _add(mode_data.get("provider", ""))
        except Exception:  # noqa: BLE001
            pass  # best-effort

        # 3. ai.routing.models — HybridRouter tier slots
        try:
            routing_cfg = ai_cfg.get("routing") or {}
            models_cfg = routing_cfg.get("models") or {}
            for _tier, slot_data in models_cfg.items():
                if isinstance(slot_data, dict):
                    _add(slot_data.get("provider", ""))
        except Exception:  # noqa: BLE001
            pass  # best-effort

        return providers

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
        """Create a provider from config.

        Special-cased providers (mcp_bridge, openrouter, github_models, ollama)
        keep their custom init logic.  All other providers (xai, anthropic,
        google, groq, nvidia, mistral, cerebras, …) are created via the
        generic ``create_provider()`` factory from ``llm_providers.py``,
        with API keys resolved by ``_resolve_provider_api_key()``.
        """
        from navig.agent.llm_providers import (
            GitHubModelsProvider,
            McpBridgeProvider,
            OllamaProvider,
            OpenRouterProvider,
        )

        bridge_cfg = self._config.get("bridge", {})
        bridge_token = bridge_cfg.get("token", "")

        if name == "mcp_bridge":
            mcp_url = bridge_cfg.get("mcp_url", "")
            if not mcp_url:
                return None
            return McpBridgeProvider(base_url=mcp_url, api_key=bridge_token)

        elif name == "openrouter":
            import os

            api_key = self._config.get("openrouter_api_key", "") or os.getenv(
                "OPENROUTER_API_KEY", ""
            )
            if not api_key:
                # Try vault — get_api_key() is the correct single-arg API
                try:
                    from navig.vault import get_vault

                    api_key = get_vault().get_api_key("openrouter") or ""
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
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
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            if not token:
                return None
            return GitHubModelsProvider(api_key=token)

        elif name == "ollama":
            return OllamaProvider()

        # ── Generic provider (xai, anthropic, google, groq, …) ──
        return self._create_generic_provider(name)

    def _create_generic_provider(self, name: str):
        """Create any provider via the llm_providers factory + model_router key resolver."""
        try:
            from navig.agent.llm_providers import create_provider
            from navig.agent.model_router import _resolve_provider_api_key

            api_key = _resolve_provider_api_key(name, self._config)
            if not api_key:
                logger.warning(
                    "No API key found for provider '%s' — check vault or env var "
                    "(e.g. %s_API_KEY)",
                    name,
                    name.upper().replace("-", "_"),
                )
                return None
            return create_provider(name, api_key=api_key)
        except Exception:  # noqa: BLE001
            logger.debug("Cannot create generic provider %s", name, exc_info=True)
            return None

    # ── Execution ───────────────────────────────────────────────────

    async def _execute(
        self,
        provider,
        provider_name: str,
        decision: RouteDecision,
        request: RouteRequest,
    ) -> str:
        """Execute a request against a specific provider."""

        # VS Code providers: pass purpose, let VS Code pick model
        if provider_name == "mcp_bridge":
            kwargs: dict[str, Any] = {"purpose": decision.purpose}
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

        # If executing a fallback provider (not the intended primary), the model
        # in decision.model was chosen for the primary provider (e.g.
        # "meta/llama-3.1-8b-instruct" for NVIDIA). Passing that provider-specific
        # model ID to github_models/openrouter/etc causes 400/404, breaking every
        # fallback. Resolve the correct model for the actual provider being called.
        _primary_prov = decision.provider or ""
        if model and _primary_prov and provider_name != _primary_prov:
            _prefs = MODE_MODEL_PREFERENCE.get(decision.mode, {})
            _fallback_model = _prefs.get(provider_name, "")
            if _fallback_model:
                model = _fallback_model
            else:
                _fb_default = (
                    _PROVIDER_DEFAULT_MODELS.get(provider_name, {}).get(decision.mode, "")
                    or _PROVIDER_DEFAULT_MODELS.get(provider_name, {}).get("_default", "")
                )
                if _fb_default:
                    model = _fb_default
                # else keep decision.model — call will likely fail but
                # the exception is caught by the surrounding loop.

        if not model:
            prefs = MODE_MODEL_PREFERENCE.get(decision.mode, {})
            model = prefs.get(provider_name, "")

        # Emergency fallback: use per-provider hardcoded default when no model
        # was resolved from config (e.g. NVIDIA activated with empty model list).
        if not model:
            model = _PROVIDER_DEFAULT_MODELS.get(provider_name, {}).get(
                decision.mode, ""
            ) or _PROVIDER_DEFAULT_MODELS.get(provider_name, {}).get("_default", "")

        if not model:
            raise RuntimeError(f"No model configured for {provider_name}/{decision.mode}")

        # Language-aware model override: French users get Mistral on github_models.
        lang = (request.metadata or {}).get("detected_language", "")
        if lang == "fr" and provider_name == "github_models":
            model = (
                "Mistral-Nemo"
                if decision.mode in ("small_talk", "summarize")
                else "Mistral-large-2407"
            )

        # Guard: log a warning if Opus is about to be used — it must never auto-route.
        if "claude-opus" in model.lower():
            logger.warning(
                "[routing] Opus model selected: provider=%s mode=%s model=%s — "
                "verify this is an explicit user request (allow_premium=True)",
                provider_name,
                decision.mode,
                model,
            )

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
        self,
        request: RouteRequest,
        decision: RouteDecision,
        trace: RouteTrace,
        provider_chain: list[str],
        t0: float,
    ) -> str | None:
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
        for name in provider_chain[current_idx + 1 :]:
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
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        self._providers.clear()


# ── Singleton ───────────────────────────────────────────────────────

_router: UnifiedRouter | None = None


def get_router(config: dict[str, Any] | None = None) -> UnifiedRouter:
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
        try:
            import asyncio

            loop = asyncio.get_running_loop()
            loop.create_task(_router.close())
        except RuntimeError:
            asyncio.run(_router.close())
    _router = None


def _extract_user_text(messages: list[dict[str, str]]) -> str:
    """Extract the last user message text."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    if messages:
        return messages[-1].get("content", "")
    return ""
