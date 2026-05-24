"""
NAVIG AI Providers Package

Multi-provider AI system with fallback support.

Lazy-loading design: importing ``navig.providers`` costs ~0 ms.
Sub-modules are imported only when an attribute is first accessed.
This prevents Windows Defender / AV .pyc scanning from blocking gateway startup.
"""

from __future__ import annotations

import importlib
from typing import Any

# ---------------------------------------------------------------------------
# Attribute → sub-module map (relative paths from this package)
# ---------------------------------------------------------------------------
# Each entry: "ExportedName" → ".submodule"
# For aliases: "ExportedName" → (".submodule", "actual_attr_in_module")
# ---------------------------------------------------------------------------

_LAZY: dict[str, Any] = {
    # ── types ─────────────────────────────────────────────────────────────
    "BUILTIN_PROVIDERS":        ".types",
    "PROVIDER_ENV_VARS":        ".types",
    "ApiKeyCredential":         ".types",
    "AuthMode":                 ".types",
    "AuthProfileStore":         ".types",
    "ModelApi":                 ".types",
    "ModelCompatConfig":        ".types",
    "ModelCost":                ".types",
    "ModelDefinition":          ".types",
    "OAuthCredential":          ".types",
    "ProviderConfig":           ".types",
    "ProvidersConfig":          ".types",
    "TokenCredential":          ".types",
    "builtin_provider_configs": ".types",
    # ── capabilities ──────────────────────────────────────────────────────
    "Capability":                   ".capabilities",
    "ModelCapabilityEntry":         ".capabilities",
    "capabilities_label":           ".capabilities",
    "get_model_capabilities":       ".capabilities",
    "has_capability":               ".capabilities",
    "list_models_with_capability":  ".capabilities",
    "list_vision_models":           ".capabilities",
    # ── discovery ─────────────────────────────────────────────────────────
    "ModelInfo":                ".discovery",
    "get_vision_api_format":    ".discovery",
    "list_available_models":    ".discovery",
    "list_connected_providers": ".discovery",
    "resolve_vision_model":     ".discovery",
    # alias: ProviderInfo → DiscoveryProviderInfo
    "DiscoveryProviderInfo":    (".discovery", "ProviderInfo"),
    # ── fallback ──────────────────────────────────────────────────────────
    "FallbackCandidate":    ".fallback",
    "FallbackManager":      ".fallback",
    "FallbackResult":       ".fallback",
    "complete_with_fallback": ".fallback",
    "get_fallback_manager": ".fallback",
    # ── oauth ─────────────────────────────────────────────────────────────
    "OAUTH_PROVIDERS":            ".oauth",
    "OAuthCredentials":           ".oauth",
    "OAuthFlowResult":            ".oauth",
    "OAuthProviderConfig":        ".oauth",
    "exchange_code_for_tokens":   ".oauth",
    "generate_pkce_pair":         ".oauth",
    "generate_state":             ".oauth",
    "refresh_oauth_tokens":       ".oauth",
    "run_oauth_flow_headless":    ".oauth",
    "run_oauth_flow_interactive": ".oauth",
    # ── registry ──────────────────────────────────────────────────────────
    "ALL_PROVIDERS":        ".registry",
    "ProviderManifest":     ".registry",
    "ProviderTier":         ".registry",
    "get_provider":         ".registry",
    "list_all_providers":   ".registry",
    "list_enabled_providers": ".registry",
    # ── auth  (SLOW on Windows — lazy-load keeps startup fast) ────────────
    "AuthProfileManager":   ".auth",
    # ── clients ───────────────────────────────────────────────────────────
    "AnthropicClient":      ".clients",
    "BaseProviderClient":   ".clients",
    "CompletionRequest":    ".clients",
    "CompletionResponse":   ".clients",
    "Message":              ".clients",
    "OpenAIClient":         ".clients",
    "ProviderError":        ".clients",
    "ToolCall":             ".clients",
    "ToolDefinition":       ".clients",
    "create_client":        ".clients",
    "get_builtin_provider": ".clients",
    # ── airllm  (SLOW on Windows — lazy-load keeps startup fast) ──────────
    "AirLLMClient":                  ".airllm",
    "AirLLMConfig":                  ".airllm",
    "create_airllm_client":          ".airllm",
    "get_airllm_vram_recommendations": ".airllm",
    "is_airllm_available":           ".airllm",
}

# Names that belong to the optional perplexity sub-module
_PERPLEXITY_NAMES = frozenset({
    "PERPLEXITY_PROVIDER",
    "PerplexityClient",
    "PerplexitySearchResult",
    "create_perplexity_client",
    "is_perplexity_available",
    "perplexity_search",
})


def __getattr__(name: str) -> Any:
    # ── optional perplexity ───────────────────────────────────────────────
    if name in _PERPLEXITY_NAMES:
        return _load_perplexity(name)

    # ── standard lazy lookup ──────────────────────────────────────────────
    entry = _LAZY.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    if isinstance(entry, tuple):
        submod_rel, attr = entry
    else:
        submod_rel, attr = entry, name

    mod = importlib.import_module(submod_rel, package=__name__)
    value = getattr(mod, attr)

    # Cache on this module so subsequent accesses bypass __getattr__
    globals()[name] = value
    return value


def _load_perplexity(name: str) -> Any:
    try:
        mod = importlib.import_module(".perplexity", package=__name__)
        value = getattr(mod, name)
    except ImportError:
        if name == "is_perplexity_available":
            value: Any = lambda: False
        else:
            value = None

    globals()[name] = value
    return value


__all__ = [
    # Types
    "ModelApi",
    "AuthMode",
    "ModelCost",
    "ModelCompatConfig",
    "ModelDefinition",
    "ProviderConfig",
    "ProvidersConfig",
    "ApiKeyCredential",
    "TokenCredential",
    "OAuthCredential",
    "AuthProfileStore",
    "BUILTIN_PROVIDERS",
    "PROVIDER_ENV_VARS",
    "builtin_provider_configs",
    # Capabilities
    "Capability",
    "ModelCapabilityEntry",
    "get_model_capabilities",
    "has_capability",
    "list_vision_models",
    "list_models_with_capability",
    "capabilities_label",
    # Discovery
    "DiscoveryProviderInfo",
    "ModelInfo",
    "list_connected_providers",
    "list_available_models",
    "resolve_vision_model",
    "get_vision_api_format",
    # Registry
    "ProviderManifest",
    "ProviderTier",
    "ALL_PROVIDERS",
    "get_provider",
    "list_enabled_providers",
    "list_all_providers",
    # Auth
    "AuthProfileManager",
    # Clients
    "Message",
    "ToolDefinition",
    "CompletionRequest",
    "CompletionResponse",
    "ToolCall",
    "ProviderError",
    "BaseProviderClient",
    "OpenAIClient",
    "AnthropicClient",
    "create_client",
    "get_builtin_provider",
    # AirLLM
    "AirLLMConfig",
    "AirLLMClient",
    "create_airllm_client",
    "is_airllm_available",
    "get_airllm_vram_recommendations",
    # Perplexity
    "PerplexityClient",
    "PerplexitySearchResult",
    "create_perplexity_client",
    "perplexity_search",
    "is_perplexity_available",
    "PERPLEXITY_PROVIDER",
    # Fallback
    "FallbackCandidate",
    "FallbackResult",
    "FallbackManager",
    "get_fallback_manager",
    "complete_with_fallback",
    # OAuth
    "OAuthCredentials",
    "OAuthProviderConfig",
    "OAuthFlowResult",
    "OAUTH_PROVIDERS",
    "generate_pkce_pair",
    "generate_state",
    "run_oauth_flow_interactive",
    "run_oauth_flow_headless",
    "exchange_code_for_tokens",
    "refresh_oauth_tokens",
]
