"""
LLM Mode Router — Multi-mode routing with uncensored support.

Routes user requests to the optimal LLM provider/model based on
task classification (small_talk, big_tasks, coding, summarize, research).

Supports:
  - Automatic mode detection from user input
  - Uncensored model routing (local Ollama or API)
  - Fallback chains per mode
  - Full backward compat (no llm_modes → legacy NAVIG_AI_MODEL path)

Usage:
    router = get_llm_router()
    mode = router.detect_mode(user_text)
    resolved = router.get_config(mode, prefer_uncensored=True)
    # → ResolvedLLMConfig(provider="ollama", model="dolphin-llama3:8b", ...)

──────────────────────────────────────────────────────────────────────────────
ARCHITECTURE NOTE: Two-Layer LLM Routing
──────────────────────────────────────────────────────────────────────────────
NAVIG has two router modules that serve orthogonal purposes. Use the right one:

  navig.llm_router           ← THIS FILE
    Layer 1 — MODE routing.  Picks WHAT TYPE of LLM task to perform.
    Consumers: llm_generate.py, commands/mode.py, gateway/deck_api.py,
               gateway/channels/telegram.py, cli commands.
    Key types:  LLMModeRouter, ResolvedLLMConfig, get_llm_router()

  navig.agent.model_router   ← AGENT RUNTIME
    Layer 2 — TIER routing.  Picks WHICH MODEL SIZE to call (small/big/coder).
    Consumers: agent/ai_client.py, agent/conversational.py only.
    Key types:  HybridRouter, RoutingDecision, ModelSlot, RoutingConfig

agent/conversational.py correctly orchestrates BOTH layers:
  • llm_router → selects mode (coding vs chat)
  • model_router → selects tier (3b vs 70b)
DO NOT merge these two modules.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT
from navig.providers.source_scan import PROVIDER_ENV_KEYS

try:
    from pydantic import BaseModel, ConfigDict, Field, field_validator

    PYDANTIC_OK = True
except ImportError:
    PYDANTIC_OK = False

logger = logging.getLogger("navig.llm_router")

# ─────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────

CANONICAL_MODES = {"small_talk", "big_tasks", "coding", "summarize", "research"}

MODE_ALIASES: dict[str, str] = {
    # small_talk
    "small": "small_talk",
    "chat": "small_talk",
    "casual": "small_talk",
    "talk": "small_talk",
    "hi": "small_talk",
    "hello": "small_talk",
    # big_tasks
    "big": "big_tasks",
    "complex": "big_tasks",
    "plan": "big_tasks",
    "reason": "big_tasks",
    "think": "big_tasks",
    # coding
    "code": "coding",
    "dev": "coding",
    "debug": "coding",
    "program": "coding",
    "script": "coding",
    # summarize
    "sum": "summarize",
    "summary": "summarize",
    "tl;dr": "summarize",
    "tldr": "summarize",
    "digest": "summarize",
    # research
    "research": "research",
    "analysis": "research",
    "compare": "research",
    "sources": "research",
    "analyze": "research",
    "study": "research",
}

# Providers that enforce content filtering (censored)
CENSORED_PROVIDERS: set[str] = {"openai", "anthropic", "deepseek", "google"}

# ---------------------------------------------------------------------------
# Provider Resource URLs — canonical source of truth for all external endpoints
# ---------------------------------------------------------------------------
# Each provider maps to a dict of named resource URLs.  Consumer modules
# import this constant rather than building URLs ad-hoc.
#   from navig.llm_router import PROVIDER_RESOURCE_URLS as _PRUL

PROVIDER_RESOURCE_URLS: dict[str, dict[str, str]] = {
    "openai": {
        "chat": "https://api.openai.com/v1/chat/completions",
        "transcriptions": "https://api.openai.com/v1/audio/transcriptions",
        "speech": "https://api.openai.com/v1/audio/speech",
        "embeddings": "https://api.openai.com/v1/embeddings",
    },
    "deepgram": {
        "listen": "https://api.deepgram.com/v1/listen",
        "speak": "https://api.deepgram.com/v1/speak",
        "analyze": "https://api.deepgram.com/v1/read",
    },
    "elevenlabs": {
        "tts_base": "https://api.elevenlabs.io/v1/text-to-speech",
        "voices": "https://api.elevenlabs.io/v1/voices",
        "tts_stream": "https://api.elevenlabs.io/v1/text-to-speech/stream",
    },
    "google_tts": {
        "synthesize": "https://texttospeech.googleapis.com/v1/text:synthesize",
    },
    "spotify": {
        "token": "https://accounts.spotify.com/api/token",
        "search": "https://api.spotify.com/v1/search",
        "recommendations": "https://api.spotify.com/v1/recommendations",
    },
    "lastfm": {
        "base": "https://ws.audioscrobbler.com/2.0/",
    },
    "audd": {
        "base": "https://api.audd.io/",
    },
    "serpapi": {
        "search": "https://serpapi.com/search",
    },
}

# Provider → base URL
PROVIDER_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com/v1",
    "grok": "https://api.x.ai/v1",
    "xai": "https://api.x.ai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "groq": "https://api.groq.com/openai/v1",
    "google": "https://generativelanguage.googleapis.com/v1beta/openai",
    "siliconflow": "https://api.siliconflow.cn/v1",
    "mistral": "https://api.mistral.ai/v1",
    "cohere": "https://api.cohere.ai/v1",
    "together": "https://api.together.xyz/v1",
    "github_models": "https://models.inference.ai.azure.com",
    "ollama": "http://127.0.0.1:11434/v1",
    "llamacpp": "http://127.0.0.1:8080/v1",
    "mcp_bridge": f"ws://127.0.0.1:{BRIDGE_DEFAULT_PORT}",
}

SUPPORTED_PROVIDERS = set(PROVIDER_BASE_URLS.keys())

# ─────────────────────────────────────────────────────────────
# Pydantic Schemas
# ─────────────────────────────────────────────────────────────

if PYDANTIC_OK:

    class LLMModeConfig(BaseModel):
        """Configuration for a single LLM mode."""

        description: str = ""
        provider: str = "ollama"
        model: str = ""
        fallback_model: str = ""
        fallback_provider: str = ""  # if empty, same as provider
        temperature: float = Field(default=0.7, ge=0.0, le=2.0)
        max_tokens: int = Field(default=4096, ge=1, le=131072)
        use_uncensored: bool = False

        model_config = ConfigDict(extra="allow")

        @field_validator("provider")
        @classmethod
        def validate_provider(cls, v: str) -> str:
            v = v.lower().strip()
            if v and v not in SUPPORTED_PROVIDERS:
                logger.warning("Unknown provider '%s' — may still work if OpenAI-compatible", v)
            return v

    class UncensoredLocalModels(BaseModel):
        """Map of alias → local Ollama model name for uncensored routing."""

        model_config = ConfigDict(extra="allow")
        # populated dynamically from config, no fixed fields

    class UncensoredApiModels(BaseModel):
        """Map of alias → API model ID for uncensored routing."""

        model_config = ConfigDict(extra="allow")

    class UncensoredOverrides(BaseModel):
        """Uncensored model override configuration."""

        enabled: bool = True
        local_models: dict[str, str] = Field(default_factory=dict)
        api_models: dict[str, str] = Field(default_factory=dict)

        model_config = ConfigDict(extra="allow")

    class LLMModesConfig(BaseModel):
        """Top-level llm_modes configuration block."""

        small_talk: LLMModeConfig = Field(
            default_factory=lambda: LLMModeConfig(
                description="Fast, conversational, personality-driven chat",
                # AUDIT self-check: Correct implementation? yes - restores documented defaults.
                # AUDIT self-check: Break callers? no - user config still overrides these values.
                # AUDIT self-check: Simpler alternative? yes - default provider swap only.
                provider="ollama",
                model="qwen2.5:3b-instruct",
                fallback_model="qwen2.5:3b-instruct",
                fallback_provider="ollama",
                temperature=0.8,
                max_tokens=1024,
                use_uncensored=False,
            )
        )
        big_tasks: LLMModeConfig = Field(
            default_factory=lambda: LLMModeConfig(
                description="Complex reasoning, planning, multi-step tasks",
                provider="openai",
                model="gpt-4o",
                fallback_model="qwen2.5:7b-instruct",
                fallback_provider="ollama",
                temperature=0.5,
                max_tokens=4096,
                use_uncensored=False,
            )
        )
        coding: LLMModeConfig = Field(
            default_factory=lambda: LLMModeConfig(
                description="Code generation, review, debugging",
                provider="deepseek",
                model="deepseek-coder",
                fallback_model="qwen2.5:7b-instruct",
                fallback_provider="ollama",
                temperature=0.2,
                max_tokens=4096,
                use_uncensored=False,
            )
        )
        summarize: LLMModeConfig = Field(
            default_factory=lambda: LLMModeConfig(
                description="Cheap, fast summarization of long text and logs",
                provider="ollama",
                model="qwen2.5:3b-instruct",
                fallback_model="qwen2.5:3b-instruct",
                fallback_provider="ollama",
                temperature=0.3,
                max_tokens=2048,
                use_uncensored=False,
            )
        )
        research: LLMModeConfig = Field(
            default_factory=lambda: LLMModeConfig(
                description="Long-context, tool-using research and document analysis",
                provider="deepseek",
                model="deepseek-chat",
                fallback_model="qwen2.5:7b-instruct",
                fallback_provider="ollama",
                temperature=0.4,
                max_tokens=4096,
                use_uncensored=False,
            )
        )

        model_config = ConfigDict(extra="allow")

        def get_mode(self, name: str) -> LLMModeConfig | None:
            """Get mode config by canonical name."""
            return getattr(self, name, None)

        def set_mode(self, name: str, cfg: LLMModeConfig) -> None:
            """Set mode config by canonical name."""
            if name in CANONICAL_MODES:
                setattr(self, name, cfg)

        def to_dict(self) -> dict[str, Any]:
            """Serialize all modes."""
            return {m: getattr(self, m).model_dump() for m in CANONICAL_MODES}

    class LLMRouterConfig(BaseModel):
        """Full router configuration block (stored under 'llm_router' in config)."""

        llm_modes: LLMModesConfig = Field(default_factory=LLMModesConfig)
        uncensored_overrides: UncensoredOverrides = Field(
            default_factory=lambda: UncensoredOverrides(
                enabled=True,
                local_models={
                    "dolphin": "dolphin-llama3:8b",
                    "hermes": "nous-hermes-llama3:8b",
                    "dolphin_small": "dolphin3:3b",
                },
                api_models={
                    "grok": "grok-beta",
                    "dolphin_api": "cognitivecomputations/dolphin-llama-3-70b",
                },
            )
        )

        model_config = ConfigDict(extra="allow")

else:
    # Fallback: plain dicts if Pydantic not available
    LLMModeConfig = dict
    LLMModesConfig = dict
    UncensoredOverrides = dict
    LLMRouterConfig = dict


# ─────────────────────────────────────────────────────────────
# Resolved Config (output of routing)
# ─────────────────────────────────────────────────────────────


class ResolvedLLMConfig:
    """Result of mode routing — everything needed to make an LLM call."""

    __slots__ = (
        "provider",
        "model",
        "base_url",
        "temperature",
        "max_tokens",
        "is_uncensored",
        "resolution_reason",
        "mode",
        "api_key_env",
    )

    def __init__(
        self,
        provider: str,
        model: str,
        base_url: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        is_uncensored: bool = False,
        resolution_reason: str = "",
        mode: str = "big_tasks",
        api_key_env: str = "",
    ):
        self.provider = provider
        self.model = model
        self.base_url = base_url or PROVIDER_BASE_URLS.get(provider, "")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.is_uncensored = is_uncensored
        self.resolution_reason = resolution_reason
        self.mode = mode
        self.api_key_env = api_key_env

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "is_uncensored": self.is_uncensored,
            "resolution_reason": self.resolution_reason,
            "mode": self.mode,
            "api_key_env": self.api_key_env,
        }

    def __repr__(self) -> str:
        return (
            f"ResolvedLLMConfig(mode={self.mode!r}, provider={self.provider!r}, "
            f"model={self.model!r}, uncensored={self.is_uncensored})"
        )


# ────────────────────────────────────────────────────────────────
# Mode Detection — canonical impl lives in navig.routing.detect
# LLMModeRouter.detect_mode() delegates there; no duplicate here.
# ────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# Ollama Availability Check
# ─────────────────────────────────────────────────────────────

_ollama_model_cache: dict[str, bool] | None = None
_ollama_cache_ts: float = 0.0


def _check_ollama_models(base_url: str = "http://127.0.0.1:11434") -> dict[str, bool]:
    """Query Ollama /api/tags and return {model_name: True} for installed models."""
    global _ollama_model_cache, _ollama_cache_ts
    import time

    now = time.time()
    if _ollama_model_cache is not None and (now - _ollama_cache_ts) < 300:
        return _ollama_model_cache

    try:
        import httpx

        resp = httpx.get(f"{base_url}/api/tags", timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            models = {}
            for m in data.get("models", []):
                name = m.get("name", "")
                models[name] = True
                # Also index without tag (e.g. "dolphin-llama3" from "dolphin-llama3:8b")
                base = name.split(":")[0]
                models[base] = True
            # Only cache on a real successful response — never cache empty/failure so
            # transient timeouts don't black-hole uncensored routing for 5 minutes.
            _ollama_model_cache = models
            _ollama_cache_ts = now
            return models
        # Non-200 response: fall through, do NOT write to cache
    except Exception:  # noqa: BLE001
        # Network/timeout error: return empty but do NOT cache — caller will retry next invocation
        logger.debug("Ollama probe failed — skipping cache update to allow immediate retry")
        return {}

    return {}


def _resolve_api_key(provider: str) -> str | None:
    """Resolve API key from environment variables, vault, or config."""
    env_vars = list(PROVIDER_ENV_KEYS.get(provider, []))
    manifest = None
    try:
        from navig.providers.registry import get_provider

        manifest = get_provider(provider)
        if manifest and manifest.env_vars:
            for var in manifest.env_vars:
                if var and var not in env_vars:
                    env_vars.append(var)
    except (ImportError, AttributeError, RuntimeError) as exc:
        logger.debug("Provider manifest lookup failed for %s: %s", provider, exc)

    for var in env_vars:
        val = os.environ.get(var)
        if val:
            return val
    # Also try vault
    try:
        from navig.vault import get_vault

        vault = get_vault()
        key = vault.get_api_key(provider)
        if key:
            return key
    except (ImportError, AttributeError, RuntimeError) as exc:
        logger.debug("Vault API key lookup failed for %s: %s", provider, exc)
    # For github_models, also check config.yaml
    if provider == "github_models":
        try:
            from navig.config import get_config_manager

            cfg = get_config_manager().global_config or {}
            token = cfg.get("github_models", {}).get("token", "")
            if token:
                return token
        except (ImportError, AttributeError, RuntimeError) as exc:
            logger.debug("github_models config token lookup failed: %s", exc)

    # Finally, best-effort lookup via vault labels using provider manifest keys.
    try:
        if manifest is None:
            from navig.providers.registry import get_provider

            manifest = get_provider(provider)

        if manifest and manifest.vault_keys:
            from navig.vault.core import get_vault

            vault = get_vault()
            for path in manifest.vault_keys:
                if not path:
                    continue
                try:
                    key = vault.get_secret(path)
                except (KeyError, AttributeError, TypeError, ValueError):
                    key = None
                if key:
                    return key
        # Env-var names as vault labels (covers keys stored as e.g. 'github_token',
        # 'openai_api_key', or any casing the user chose when running navig vault set)
        if manifest and manifest.env_vars:
            from navig.vault.core import get_vault

            vault = get_vault()
            _tried: set[str] = {provider} | set(manifest.vault_keys or [])
            for var in manifest.env_vars:
                var_lower = var.lower()
                if var_lower in _tried:
                    continue
                _tried.add(var_lower)
                try:
                    key = vault.get_api_key(var_lower)
                    if key:
                        return key
                except Exception:  # noqa: BLE001
                    pass
                try:
                    key = vault.get_secret(var_lower)
                    if key:
                        return key
                except (KeyError, AttributeError, TypeError, ValueError):
                    pass
    except (ImportError, AttributeError, RuntimeError) as exc:
        logger.debug("Vault label lookup failed for %s: %s", provider, exc)

    return None


def _has_api_key(provider: str) -> bool:
    """Check if an API key is available for a provider."""
    if provider in {"ollama", "llamacpp", "airllm", "mcp_bridge"}:
        return True  # no key needed
    return _resolve_api_key(provider) is not None


# ─────────────────────────────────────────────────────────────
# LLMModeRouter
# ─────────────────────────────────────────────────────────────


class LLMModeRouter:
    """
    Multi-mode LLM router with uncensored support.

    Resolves mode → provider + model, handling:
      - Alias resolution (chat → small_talk, code → coding, etc.)
      - Uncensored override routing (local Ollama → API fallback)
      - Fallback chains
      - API key availability checks
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self._raw_config = config or {}
        # ai.default_provider — user-level provider override applied after mode routing
        self._default_provider: str = (self._raw_config.get("ai") or {}).get(
            "default_provider"
        ) or ""
        self._router_config: LLMRouterConfig | None = None
        self._load_config()

    def _load_config(self) -> None:
        """Parse and validate the router configuration."""
        if not PYDANTIC_OK:
            logger.warning("Pydantic not installed — LLM router using raw dicts")
            self._router_config = None
            return

        try:
            # The config block may be at top-level or under 'llm_router'
            raw = self._raw_config
            if "llm_router" in raw:
                raw = raw["llm_router"]
            elif "llm_modes" not in raw:
                # No mode config at all — will use defaults
                raw = {}

            self._router_config = LLMRouterConfig.model_validate(raw)
            logger.debug("LLM router config loaded: %d modes", len(CANONICAL_MODES))
        except Exception as e:
            logger.error("Failed to parse LLM router config: %s", e)
            self._router_config = LLMRouterConfig()

    @property
    def config(self) -> LLMRouterConfig:
        if self._router_config is None:
            self._router_config = LLMRouterConfig()
        return self._router_config

    @property
    def modes(self) -> LLMModesConfig:
        return self.config.llm_modes

    @property
    def uncensored(self) -> UncensoredOverrides:
        return self.config.uncensored_overrides

    # ── Alias resolution ──────────────────────────────────

    @staticmethod
    def resolve_mode(hint: str) -> str:
        """Resolve a mode hint/alias to a canonical mode name."""
        h = hint.lower().strip()
        if h in CANONICAL_MODES:
            return h
        return MODE_ALIASES.get(h, "big_tasks")

    def detect_mode(self, user_input: str) -> str:
        """Heuristic mode detection from user text."""
        return detect_mode(user_input)

    def _pick_model_for_provider(self, provider: str, mode: str, current_model: str) -> str:
        """Select a model compatible with *provider*, preferring the current mode mapping."""
        provider = (provider or "").strip().lower()
        if not provider:
            return current_model

        mode_cfg = self.modes.get_mode(mode)
        if mode_cfg and mode_cfg.provider == provider and mode_cfg.model:
            return mode_cfg.model

        for canonical_mode in ("small_talk", "big_tasks", "coding", "summarize", "research"):
            cfg = self.modes.get_mode(canonical_mode)
            if cfg and cfg.provider == provider and cfg.model:
                return cfg.model

        return current_model

    # ── Main routing logic ────────────────────────────────

    def _get_config_impl(
        self,
        mode_hint: str,
        prefer_uncensored: bool | None = None,
    ) -> ResolvedLLMConfig:
        """
        Resolve a mode hint into a full LLM configuration.

        Args:
            mode_hint: Canonical mode name or alias.
            prefer_uncensored: Override the mode's use_uncensored setting.

        Returns:
            ResolvedLLMConfig with provider, model, params, and reason.
        """
        mode = self.resolve_mode(mode_hint)
        mode_cfg = self.modes.get_mode(mode)

        if mode_cfg is None:
            return ResolvedLLMConfig(
                provider="openai",
                model="gpt-4o-mini",
                mode=mode,
                resolution_reason=f"Unknown mode '{mode}', using default",
            )

        want_uncensored = (
            prefer_uncensored if prefer_uncensored is not None else mode_cfg.use_uncensored
        )

        # If uncensored is requested and the primary provider is censored,
        # attempt to route to an uncensored model
        if want_uncensored and self.uncensored.enabled:
            resolved = self._try_uncensored_routing(mode, mode_cfg)
            if resolved:
                return resolved

        # Standard routing: use the mode's configured provider/model
        provider = mode_cfg.provider
        model = mode_cfg.model

        # Check if provider has API key (or is local)
        if not _has_api_key(provider):
            # Try fallback
            fb_provider = mode_cfg.fallback_provider or provider
            fb_model = mode_cfg.fallback_model
            if fb_model and _has_api_key(fb_provider):
                return ResolvedLLMConfig(
                    provider=fb_provider,
                    model=fb_model,
                    temperature=mode_cfg.temperature,
                    max_tokens=mode_cfg.max_tokens,
                    is_uncensored=False,
                    mode=mode,
                    resolution_reason=f"Primary {provider} has no API key, using fallback {fb_provider}:{fb_model}",
                    api_key_env=_get_env_var_name(fb_provider),
                )
            return ResolvedLLMConfig(
                provider=provider,
                model=model,
                temperature=mode_cfg.temperature,
                max_tokens=mode_cfg.max_tokens,
                is_uncensored=False,
                mode=mode,
                resolution_reason=f"No API key for {provider} (checked: {PROVIDER_ENV_KEYS.get(provider, [])})",
                api_key_env=_get_env_var_name(provider),
            )

        return ResolvedLLMConfig(
            provider=provider,
            model=model,
            temperature=mode_cfg.temperature,
            max_tokens=mode_cfg.max_tokens,
            is_uncensored=False,
            mode=mode,
            resolution_reason=f"Direct route: {provider}:{model}",
            api_key_env=_get_env_var_name(provider),
        )

    def get_config(
        self,
        mode_hint: str,
        prefer_uncensored: bool | None = None,
    ) -> ResolvedLLMConfig:
        """Resolve a mode hint into a full LLM configuration.

        Applies ``ai.default_provider`` override when set — lets the user pin a
        preferred provider without touching per-mode config.
        """
        resolved = self._get_config_impl(mode_hint, prefer_uncensored)
        if self._default_provider and resolved.provider != self._default_provider:
            original_provider = resolved.provider
            original_model = resolved.model
            resolved.provider = self._default_provider
            resolved.model = self._pick_model_for_provider(
                self._default_provider,
                resolved.mode,
                resolved.model,
            )
            resolved.base_url = PROVIDER_BASE_URLS.get(self._default_provider, "")
            resolved.api_key_env = _get_env_var_name(self._default_provider)
            resolved.resolution_reason = (
                f"{resolved.resolution_reason} "
                f"[default_provider override: {original_provider}:{original_model} -> "
                f"{resolved.provider}:{resolved.model}]"
            )
        return resolved

    def _try_uncensored_routing(
        self, mode: str, mode_cfg: LLMModeConfig
    ) -> ResolvedLLMConfig | None:
        """
        Attempt uncensored routing.

        Order:
          1. Local Ollama uncensored model (if available)
          2. Uncensored API model (Grok, OpenRouter, etc.)
          3. None (caller falls back to standard routing)
        """
        # 1. Try local Ollama uncensored models
        local_models = self.uncensored.local_models
        if local_models:
            installed = _check_ollama_models()
            for alias, model_name in local_models.items():
                base = model_name.split(":")[0]
                if model_name in installed or base in installed:
                    return ResolvedLLMConfig(
                        provider="ollama",
                        model=model_name,
                        temperature=mode_cfg.temperature,
                        max_tokens=mode_cfg.max_tokens,
                        is_uncensored=True,
                        mode=mode,
                        resolution_reason=f"Uncensored local: {alias} → ollama:{model_name}",
                    )

        # 2. Try uncensored API models
        api_models = self.uncensored.api_models
        for alias, model_name in api_models.items():
            # Work out which provider hosts this model
            provider = _infer_provider_for_uncensored(alias, model_name)
            if _has_api_key(provider):
                return ResolvedLLMConfig(
                    provider=provider,
                    model=model_name,
                    temperature=mode_cfg.temperature,
                    max_tokens=mode_cfg.max_tokens,
                    is_uncensored=True,
                    mode=mode,
                    resolution_reason=f"Uncensored API: {alias} → {provider}:{model_name}",
                    api_key_env=_get_env_var_name(provider),
                )

        return None

    # ── Mode management ───────────────────────────────────

    def get_all_modes(self) -> dict[str, dict[str, Any]]:
        """Return all mode configs as dict."""
        return self.modes.to_dict()

    def update_mode(
        self,
        mode: str,
        provider: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        use_uncensored: bool | None = None,
    ) -> bool:
        """Update a mode's configuration in memory."""
        mode = self.resolve_mode(mode)
        cfg = self.modes.get_mode(mode)
        if cfg is None:
            return False
        if provider is not None:
            cfg.provider = provider
        if model is not None:
            cfg.model = model
        if temperature is not None:
            cfg.temperature = temperature
        if max_tokens is not None:
            cfg.max_tokens = max_tokens
        if use_uncensored is not None:
            cfg.use_uncensored = use_uncensored
        self.modes.set_mode(mode, cfg)
        return True

    def list_uncensored_models(self) -> dict[str, Any]:
        """
        List available uncensored models (local + API) with availability status.
        """
        result: dict[str, Any] = {"local": [], "api": []}

        # Local
        installed = _check_ollama_models()
        for alias, model in self.uncensored.local_models.items():
            base = model.split(":")[0]
            available = model in installed or base in installed
            result["local"].append(
                {
                    "alias": alias,
                    "model": model,
                    "available": available,
                    "provider": "ollama",
                }
            )

        # API
        for alias, model in self.uncensored.api_models.items():
            provider = _infer_provider_for_uncensored(alias, model)
            has_key = _has_api_key(provider)
            result["api"].append(
                {
                    "alias": alias,
                    "model": model,
                    "provider": provider,
                    "api_key_present": has_key,
                }
            )

        return result


# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────


def _infer_provider_for_uncensored(alias: str, model: str) -> str:
    """Infer provider from uncensored model alias/name."""
    a = alias.lower()
    m = model.lower()
    if "grok" in a or "grok" in m or "x.ai" in m:
        return "grok"
    if "/" in m:
        # OpenRouter-style model ID (org/model)
        return "openrouter"
    return "openrouter"  # default to OpenRouter for API models


def _get_env_var_name(provider: str) -> str:
    """Get the primary env var name for a provider."""
    keys = PROVIDER_ENV_KEYS.get(provider, [])
    return keys[0] if keys else ""


# ─────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────

_router_instance: LLMModeRouter | None = None
_router_instance_lock = threading.Lock()


def get_llm_router(force_new: bool = False) -> LLMModeRouter:
    """
    Get the global LLMModeRouter singleton.

    Loads config from get_config_manager() if available.
    If llm_modes is not configured, the router still works
    with sensible defaults.
    """
    global _router_instance
    if _router_instance is not None and not force_new:
        return _router_instance

    with _router_instance_lock:
        if _router_instance is not None and not force_new:
            return _router_instance

        config = {}
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            raw = cm.global_config or {}
            # Look for llm_router or llm_modes at top level
            if "llm_router" in raw or "llm_modes" in raw:
                config = raw
        except Exception as e:
            logger.debug("Could not load config for LLM router: %s", e)

        _router_instance = LLMModeRouter(config)
        return _router_instance


# ─────────────────────────────────────────────────────────────
# Convenience function (integration point)
# ─────────────────────────────────────────────────────────────

# ── Semantic Routing → Toolset Hints (MVP3 F-20) ─────────────

#: Mapping from canonical mode to suggested agent toolsets.
#: ``run_agentic()`` merges these with any explicit ``toolset=`` arg so
#: routing automatically narrows tool scope → cheaper schema, better
#: tool selection, lower latency.
MODE_TOOLSET_HINTS: dict[str, list[str]] = {
    "big_tasks": ["core", "devops", "memory"],
    "coding": ["core", "code", "search"],
    "research": ["search", "wiki", "memory"],
    "small_talk": [],  # no tools for casual chat
    "summarize": ["memory"],
}


def detect_mode(user_input: str) -> str:
    """Backward-compatible mode detector returning the canonical mode string.

    DEPRECATION: This shim adds a "research" regex that is not in routing/detect.py.
    Merge that pattern into routing/detect.py, then delete this function and
    update all callers to use ``from navig.routing.detect import detect_mode``.
    """
    text = (user_input or "").strip()
    lower = text.lower()
    if lower and re.search(
        r"\b(research|analy[sz]e|compare|differences?\s+between|investigate)\b",
        lower,
    ):
        return "research"

    from navig.routing.detect import detect_mode as _detect_canonical  # noqa: PLC0415

    mode, _, _ = _detect_canonical(text)
    return mode


def suggest_toolsets(
    user_input: str | None = None,
    *,
    mode: str | None = None,
) -> list[str]:
    """Return suggested agent toolset names for a user message.

    If *mode* is provided it is used directly; otherwise the mode is
    detected from *user_input* via :func:`detect_mode`.

    Returns an empty list when no tools should be offered (e.g. ``small_talk``).
    """
    if mode is None:
        from navig.routing.detect import detect_mode as _detect_canonical  # noqa: PLC0415

        mode, _, _ = _detect_canonical(user_input or "")
    else:
        mode = LLMModeRouter.resolve_mode(mode)
    return list(MODE_TOOLSET_HINTS.get(mode, ["core"]))


def resolve_llm(
    mode: str | None = None,
    user_input: str | None = None,
    prefer_uncensored: bool | None = None,
) -> ResolvedLLMConfig:
    """
    One-call convenience: detect or resolve mode, return full config.

    Usage:
        cfg = resolve_llm(user_input="write a python script to ...")
        # cfg.provider == "deepseek", cfg.model == "deepseek-coder", ...

        cfg = resolve_llm(mode="coding", prefer_uncensored=True)
        # cfg.provider == "ollama", cfg.model == "dolphin-llama3:8b", ...
    """
    router = get_llm_router()

    if mode:
        canonical = router.resolve_mode(mode)
    elif user_input:
        canonical = router.detect_mode(user_input)
    else:
        canonical = "big_tasks"

    return router.get_config(canonical, prefer_uncensored=prefer_uncensored)
