"""
NAVIG AI Providers Package

Multi-provider AI system with fallback support.
"""

from .types import (
    ModelApi,
    AuthMode,
    ModelCost,
    ModelCompatConfig,
    ModelDefinition,
    ProviderConfig,
    ProvidersConfig,
    ApiKeyCredential,
    TokenCredential,
    OAuthCredential,
    AuthProfileStore,
    BUILTIN_PROVIDERS,
    PROVIDER_ENV_VARS,
    builtin_provider_configs,
)

from .registry import (
    ProviderManifest,
    ProviderTier,
    ALL_PROVIDERS,
    get_provider,
    list_enabled_providers,
    list_all_providers,
)

from .auth import AuthProfileManager

from .clients import (
    Message,
    ToolDefinition,
    CompletionRequest,
    CompletionResponse,
    ToolCall,
    ProviderError,
    BaseProviderClient,
    OpenAIClient,
    AnthropicClient,
    create_client,
    get_builtin_provider,
)

from .fallback import (
    FallbackCandidate,
    FallbackResult,
    FallbackManager,
    get_fallback_manager,
    complete_with_fallback,
)

from .oauth import (
    OAuthCredentials,
    OAuthProviderConfig,
    OAuthFlowResult,
    OAUTH_PROVIDERS,
    generate_pkce_pair,
    generate_state,
    run_oauth_flow_interactive,
    run_oauth_flow_headless,
    exchange_code_for_tokens,
    refresh_oauth_tokens,
)

# AirLLM provider (optional - requires airllm package)
from .airllm import (
    AirLLMConfig,
    AirLLMClient,
    create_airllm_client,
    is_airllm_available,
    get_airllm_vram_recommendations,
)

# Perplexity provider (optional - requires httpx)
try:
    from .perplexity import (
        PerplexityClient,
        PerplexitySearchResult,
        create_perplexity_client,
        perplexity_search,
        is_perplexity_available,
        PERPLEXITY_PROVIDER,
    )
    _PERPLEXITY_AVAILABLE = True
except ImportError:
    _PERPLEXITY_AVAILABLE = False
    PerplexityClient = None
    PerplexitySearchResult = None
    create_perplexity_client = None
    perplexity_search = None
    is_perplexity_available = lambda: False
    PERPLEXITY_PROVIDER = None


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
