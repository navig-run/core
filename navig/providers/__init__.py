"""
NAVIG AI Providers Package

Multi-provider AI system with fallback support.
"""

# AirLLM provider (optional - requires airllm package)
from .airllm import (
    AirLLMClient,
    AirLLMConfig,
    create_airllm_client,
    get_airllm_vram_recommendations,
    is_airllm_available,
)
from .auth import AuthProfileManager
from .clients import (
    AnthropicClient,
    BaseProviderClient,
    CompletionRequest,
    CompletionResponse,
    Message,
    OpenAIClient,
    ProviderError,
    ToolCall,
    ToolDefinition,
    create_client,
    get_builtin_provider,
)
from .fallback import (
    FallbackCandidate,
    FallbackManager,
    FallbackResult,
    complete_with_fallback,
    get_fallback_manager,
)
from .oauth import (
    OAUTH_PROVIDERS,
    OAuthCredentials,
    OAuthFlowResult,
    OAuthProviderConfig,
    exchange_code_for_tokens,
    generate_pkce_pair,
    generate_state,
    refresh_oauth_tokens,
    run_oauth_flow_headless,
    run_oauth_flow_interactive,
)
from .registry import (
    ALL_PROVIDERS,
    ProviderManifest,
    ProviderTier,
    get_provider,
    list_all_providers,
    list_enabled_providers,
)
from .types import (
    BUILTIN_PROVIDERS,
    PROVIDER_ENV_VARS,
    ApiKeyCredential,
    AuthMode,
    AuthProfileStore,
    ModelApi,
    ModelCompatConfig,
    ModelCost,
    ModelDefinition,
    OAuthCredential,
    ProviderConfig,
    ProvidersConfig,
    TokenCredential,
    builtin_provider_configs,
)

# Perplexity provider (optional - requires httpx)
try:
    from .perplexity import (
        PERPLEXITY_PROVIDER,
        PerplexityClient,
        PerplexitySearchResult,
        create_perplexity_client,
        is_perplexity_available,
        perplexity_search,
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
