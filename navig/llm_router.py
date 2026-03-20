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

import os
import threading
import re
import logging
from typing import Any, Dict, List, Optional, Set
from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

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

MODE_ALIASES: Dict[str, str] = {
    # small_talk
    "small":    "small_talk",
    "chat":     "small_talk",
    "casual":   "small_talk",
    "talk":     "small_talk",
    "hi":       "small_talk",
    "hello":    "small_talk",
    # big_tasks
    "big":      "big_tasks",
    "complex":  "big_tasks",
    "plan":     "big_tasks",
    "reason":   "big_tasks",
    "think":    "big_tasks",
    # coding
    "code":     "coding",
    "dev":      "coding",
    "debug":    "coding",
    "program":  "coding",
    "script":   "coding",
    # summarize
    "sum":      "summarize",
    "summary":  "summarize",
    "tl;dr":    "summarize",
    "tldr":     "summarize",
    "digest":   "summarize",
    # research
    "research": "research",
    "analysis": "research",
    "compare":  "research",
    "sources":  "research",
    "analyze":  "research",
    "study":    "research",
}

# Providers that enforce content filtering (censored)
CENSORED_PROVIDERS: Set[str] = {"openai", "anthropic", "deepseek", "google"}

# ---------------------------------------------------------------------------
# Provider Resource URLs — canonical source of truth for all external endpoints
# ---------------------------------------------------------------------------
# Each provider maps to a dict of named resource URLs.  Consumer modules
# import this constant rather than building URLs ad-hoc.
#   from navig.llm_router import PROVIDER_RESOURCE_URLS as _PRUL

PROVIDER_RESOURCE_URLS: Dict[str, Dict[str, str]] = {
    "openai": {
        "chat":           "https://api.openai.com/v1/chat/completions",
        "transcriptions": "https://api.openai.com/v1/audio/transcriptions",
        "speech":         "https://api.openai.com/v1/audio/speech",
        "embeddings":     "https://api.openai.com/v1/embeddings",
    },
    "deepgram": {
        "listen":  "https://api.deepgram.com/v1/listen",
        "speak":   "https://api.deepgram.com/v1/speak",
        "analyze": "https://api.deepgram.com/v1/read",
    },
    "elevenlabs": {
        "tts_base":       "https://api.elevenlabs.io/v1/text-to-speech",
        "voices":         "https://api.elevenlabs.io/v1/voices",
        "tts_stream":     "https://api.elevenlabs.io/v1/text-to-speech/stream",
    },
    "google_tts": {
        "synthesize": "https://texttospeech.googleapis.com/v1/text:synthesize",
    },
    "spotify": {
        "token":         "https://accounts.spotify.com/api/token",
        "search":        "https://api.spotify.com/v1/search",
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

# Provider → env var(s) for API key resolution
PROVIDER_ENV_KEYS: Dict[str, List[str]] = {
    "openai":        ["OPENAI_API_KEY"],
    "anthropic":     ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
    "deepseek":      ["DEEPSEEK_API_KEY"],
    "grok":          ["GROK_API_KEY", "XAI_API_KEY"],
    "xai":           ["XAI_API_KEY", "GROK_API_KEY"],
    "openrouter":    ["OPENROUTER_API_KEY"],
    "groq":          ["GROQ_API_KEY"],
    "google":        ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "siliconflow":   ["SILICONFLOW_API_KEY"],
    "mistral":       ["MISTRAL_API_KEY"],
    "cohere":        ["COHERE_API_KEY"],
    "together":      ["TOGETHER_API_KEY"],
    "github_models": ["GITHUB_TOKEN"],  # free via GitHub PAT
    "ollama":        [],  # local, no key needed
    "mcp_bridge":     [],  # VS Code Copilot via MCP WebSocket (no key, uses tunnel)
}

# Provider → base URL
PROVIDER_BASE_URLS: Dict[str, str] = {
    "openai":        "https://api.openai.com/v1",
    "anthropic":     "https://api.anthropic.com",
    "deepseek":      "https://api.deepseek.com/v1",
    "grok":          "https://api.x.ai/v1",
    "xai":           "https://api.x.ai/v1",
    "openrouter":    "https://openrouter.ai/api/v1",
    "groq":          "https://api.groq.com/openai/v1",
    "google":        "https://generativelanguage.googleapis.com/v1beta/openai",
    "siliconflow":   "https://api.siliconflow.cn/v1",
    "mistral":       "https://api.mistral.ai/v1",
    "cohere":        "https://api.cohere.ai/v1",
    "together":      "https://api.together.xyz/v1",
    "github_models": "https://models.inference.ai.azure.com",
    "ollama":        "http://127.0.0.1:11434/v1",
    "mcp_bridge":     "ws://127.0.0.1:42070",
}

SUPPORTED_PROVIDERS = set(PROVIDER_BASE_URLS.keys())

# ─────────────────────────────────────────────────────────────
# Pydantic v2 Schemas
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
        local_models: Dict[str, str] = Field(default_factory=dict)
        api_models: Dict[str, str] = Field(default_factory=dict)

        model_config = ConfigDict(extra="allow")

    class LLMModesConfig(BaseModel):
        """Top-level llm_modes configuration block."""
        small_talk: LLMModeConfig = Field(default_factory=lambda: LLMModeConfig(
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
        ))
        big_tasks: LLMModeConfig = Field(default_factory=lambda: LLMModeConfig(
            description="Complex reasoning, planning, multi-step tasks",
            provider="openai",
            model="gpt-4o-mini",
            fallback_model="qwen2.5:7b-instruct",
            fallback_provider="ollama",
            temperature=0.5,
            max_tokens=4096,
            use_uncensored=False,
        ))
        coding: LLMModeConfig = Field(default_factory=lambda: LLMModeConfig(
            description="Code generation, review, debugging",
            provider="deepseek",
            model="deepseek-coder",
            fallback_model="qwen2.5:7b-instruct",
            fallback_provider="ollama",
            temperature=0.2,
            max_tokens=4096,
            use_uncensored=False,
        ))
        summarize: LLMModeConfig = Field(default_factory=lambda: LLMModeConfig(
            description="Cheap, fast summarization of long text and logs",
            provider="ollama",
            model="qwen2.5:3b-instruct",
            fallback_model="qwen2.5:3b-instruct",
            fallback_provider="ollama",
            temperature=0.3,
            max_tokens=2048,
            use_uncensored=False,
        ))
        research: LLMModeConfig = Field(default_factory=lambda: LLMModeConfig(
            description="Long-context, tool-using research and document analysis",
            provider="deepseek",
            model="deepseek-chat",
            fallback_model="qwen2.5:7b-instruct",
            fallback_provider="ollama",
            temperature=0.4,
            max_tokens=4096,
            use_uncensored=False,
        ))

        model_config = ConfigDict(extra="allow")

        def get_mode(self, name: str) -> Optional[LLMModeConfig]:
            """Get mode config by canonical name."""
            return getattr(self, name, None)

        def set_mode(self, name: str, cfg: LLMModeConfig) -> None:
            """Set mode config by canonical name."""
            if name in CANONICAL_MODES:
                setattr(self, name, cfg)

        def to_dict(self) -> Dict[str, Any]:
            """Serialize all modes."""
            return {m: getattr(self, m).model_dump() for m in CANONICAL_MODES}

    class LLMRouterConfig(BaseModel):
        """Full router configuration block (stored under 'llm_router' in config)."""
        llm_modes: LLMModesConfig = Field(default_factory=LLMModesConfig)
        uncensored_overrides: UncensoredOverrides = Field(default_factory=lambda: UncensoredOverrides(
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
        ))

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
        "provider", "model", "base_url", "temperature",
        "max_tokens", "is_uncensored", "resolution_reason",
        "mode", "api_key_env",
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

    def to_dict(self) -> Dict[str, Any]:
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


# ─────────────────────────────────────────────────────────────
# Mode Detection — heuristic classifier
# ─────────────────────────────────────────────────────────────

# Precompiled patterns for detect_mode
_CODE_PATTERNS = re.compile(
    r"```|def\s+\w+|class\s+\w+|function\s+\w+|import\s+\w+|"
    r"const\s+\w+|let\s+\w+|var\s+\w+|console\.|print\(|"
    r"write\s+a?\s*(script|function|class|code|program|module)|"
    r"fix\s+(this|the|my)\s*(code|bug|error|script|function)|"
    r"debug|refactor|implement|unittest|pytest|"
    r"(python|javascript|typescript|rust|go|java|c\+\+|ruby|php|bash|shell)\s+(code|script|function)|"
    r"code\s+review|pull\s+request|merge\s+conflict|git\s+diff",
    re.IGNORECASE,
)

_GREETING_PATTERNS = re.compile(
    r"^(hey|hi|hello|hola|sup|yo|what'?s?\s+up|how\s+are\s+you|good\s+(morning|afternoon|evening))\b",
    re.IGNORECASE,
)

_CASUAL_PATTERNS = re.compile(
    r"^(thanks?|thx|ok|okay|cool|nice|great|lol|haha|yes|no|sure|nah|nope|yep|yeah)\s*[!.?]*$",
    re.IGNORECASE,
)

_SUMMARIZE_PATTERNS = re.compile(
    r"\b(summarize|summary|tl;?dr|tldr|digest|condense|brief|recap|overview\s+of)\b",
    re.IGNORECASE,
)

_RESEARCH_PATTERNS = re.compile(
    r"\b(research|analyze|analysis|compare|comparison|sources|"
    r"investigate|examine|evaluate|review\s+the|study|"
    r"pros?\s+and\s+cons?|advantages?\s+and\s+disadvantages?|"
    r"what\s+are\s+the\s+differences?|deep\s+dive)\b",
    re.IGNORECASE,
)


def detect_mode(user_input: str) -> str:
    """
    Classify user input into a canonical LLM mode.

    Returns one of: 'coding', 'small_talk', 'summarize', 'research', 'big_tasks'.
    """
    text = user_input.strip()
    if not text:
        return "small_talk"

    # Short inputs (< 15 chars) that are greetings
    if len(text) < 60 and _GREETING_PATTERNS.search(text):
        return "small_talk"
    if _CASUAL_PATTERNS.match(text):
        return "small_talk"

    # Code patterns (highest signal)
    if _CODE_PATTERNS.search(text):
        return "coding"

    # Summarization
    if _SUMMARIZE_PATTERNS.search(text):
        return "summarize"

    # Research
    if _RESEARCH_PATTERNS.search(text):
        return "research"

    # Short single-sentence questions → small_talk
    if len(text) < 80 and text.count("\n") == 0 and text.endswith("?"):
        return "small_talk"

    # Default
    return "big_tasks"


# ─────────────────────────────────────────────────────────────
# Ollama Availability Check
# ─────────────────────────────────────────────────────────────

_ollama_model_cache: Optional[Dict[str, bool]] = None
_ollama_cache_ts: float = 0.0


def _check_ollama_models(base_url: str = "http://127.0.0.1:11434") -> Dict[str, bool]:
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
            _ollama_model_cache = models
            _ollama_cache_ts = now
            return models
    except Exception:
        pass

    _ollama_model_cache = {}
    _ollama_cache_ts = now
    return {}


def _resolve_api_key(provider: str) -> Optional[str]:
    """Resolve API key from environment variables, vault, or config."""
    env_vars = PROVIDER_ENV_KEYS.get(provider, [])
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
    except Exception:
        pass
    # For github_models, also check config.yaml
    if provider == "github_models":
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager().global_config or {}
            token = cfg.get("github_models", {}).get("token", "")
            if token:
                return token
        except Exception:
            pass
    return None


def _has_api_key(provider: str) -> bool:
    """Check if an API key is available for a provider."""
    if provider == "ollama":
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

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self._raw_config = config or {}
        self._router_config: Optional[LLMRouterConfig] = None
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

    # ── Main routing logic ────────────────────────────────

    def get_config(
        self,
        mode_hint: str,
        prefer_uncensored: Optional[bool] = None,
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
            prefer_uncensored
            if prefer_uncensored is not None
            else mode_cfg.use_uncensored
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

    def _try_uncensored_routing(
        self, mode: str, mode_cfg: LLMModeConfig
    ) -> Optional[ResolvedLLMConfig]:
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

    def get_all_modes(self) -> Dict[str, Dict[str, Any]]:
        """Return all mode configs as dict."""
        return self.modes.to_dict()

    def update_mode(
        self,
        mode: str,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        use_uncensored: Optional[bool] = None,
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

    def list_uncensored_models(self) -> Dict[str, Any]:
        """
        List available uncensored models (local + API) with availability status.
        """
        result: Dict[str, Any] = {"local": [], "api": []}

        # Local
        installed = _check_ollama_models()
        for alias, model in self.uncensored.local_models.items():
            base = model.split(":")[0]
            available = model in installed or base in installed
            result["local"].append({
                "alias": alias,
                "model": model,
                "available": available,
                "provider": "ollama",
            })

        # API
        for alias, model in self.uncensored.api_models.items():
            provider = _infer_provider_for_uncensored(alias, model)
            has_key = _has_api_key(provider)
            result["api"].append({
                "alias": alias,
                "model": model,
                "provider": provider,
                "api_key_present": has_key,
            })

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

_router_instance: Optional[LLMModeRouter] = None
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
            if "llm_router" in raw:
                config = raw
            elif "llm_modes" in raw:
                config = raw
        except Exception as e:
            logger.debug("Could not load config for LLM router: %s", e)

        _router_instance = LLMModeRouter(config)
        return _router_instance


# ─────────────────────────────────────────────────────────────
# Convenience function (integration point)
# ─────────────────────────────────────────────────────────────

def resolve_llm(
    mode: Optional[str] = None,
    user_input: Optional[str] = None,
    prefer_uncensored: Optional[bool] = None,
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
