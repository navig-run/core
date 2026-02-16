"""
NAVIG AI Providers - Type Definitions and Configuration Schema

Based on standard model provider architecture, adapted for Python.
Supports multiple AI providers with unified interface.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union


class ModelApi(str, Enum):
    """Supported API types for different providers."""
    OPENAI_COMPLETIONS = "openai-completions"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC_MESSAGES = "anthropic-messages"
    GOOGLE_GENERATIVE_AI = "google-generative-ai"


class AuthMode(str, Enum):
    """Authentication modes for providers."""
    API_KEY = "api-key"
    OAUTH = "oauth"
    TOKEN = "token"


class ModelInput(str, Enum):
    """Supported input modalities."""
    TEXT = "text"
    IMAGE = "image"


@dataclass
class ModelCost:
    """Cost configuration per 1M tokens (in USD * 1000 for precision)."""
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    
    def to_dict(self) -> Dict[str, float]:
        return {
            "input": self.input,
            "output": self.output,
            "cacheRead": self.cache_read,
            "cacheWrite": self.cache_write
        }


@dataclass
class ModelCompatConfig:
    """Model compatibility settings."""
    supports_store: bool = False
    supports_developer_role: bool = True
    supports_reasoning_effort: bool = False
    max_tokens_field: Literal["max_completion_tokens", "max_tokens"] = "max_tokens"


@dataclass
class ModelDefinition:
    """Definition of a model within a provider."""
    id: str
    name: str
    api: Optional[ModelApi] = None
    reasoning: bool = False
    input: List[ModelInput] = field(default_factory=lambda: [ModelInput.TEXT])
    cost: ModelCost = field(default_factory=ModelCost)
    context_window: int = 128000
    max_tokens: int = 8192
    headers: Dict[str, str] = field(default_factory=dict)
    compat: Optional[ModelCompatConfig] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "api": self.api.value if self.api else None,
            "reasoning": self.reasoning,
            "input": [i.value for i in self.input],
            "cost": self.cost.to_dict(),
            "contextWindow": self.context_window,
            "maxTokens": self.max_tokens,
            "headers": self.headers,
        }


@dataclass
class ProviderConfig:
    """Configuration for a single AI provider."""
    name: str
    base_url: str
    api_key: Optional[str] = None  # Can be literal key or env var name
    auth: AuthMode = AuthMode.API_KEY
    api: ModelApi = ModelApi.OPENAI_COMPLETIONS
    headers: Dict[str, str] = field(default_factory=dict)
    auth_header: bool = True  # Whether to send Authorization header
    models: List[ModelDefinition] = field(default_factory=list)
    enabled: bool = True
    priority: int = 100  # Lower = higher priority for fallback
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "baseUrl": self.base_url,
            "apiKey": self.api_key,
            "auth": self.auth.value,
            "api": self.api.value,
            "headers": self.headers,
            "authHeader": self.auth_header,
            "models": [m.to_dict() for m in self.models],
            "enabled": self.enabled,
            "priority": self.priority,
        }


@dataclass
class ProvidersConfig:
    """Top-level providers configuration."""
    mode: Literal["merge", "replace"] = "merge"
    default_provider: Optional[str] = None
    default_model: Optional[str] = None
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)
    fallback_order: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "defaultProvider": self.default_provider,
            "defaultModel": self.default_model,
            "providers": {k: v.to_dict() for k, v in self.providers.items()},
            "fallbackOrder": self.fallback_order,
        }


# ============================================================================
# Auth Profile Types (for credential storage)
# ============================================================================

@dataclass
class ApiKeyCredential:
    """Static API key credential."""
    provider: str
    key: str
    email: Optional[str] = None
    type: str = field(default="api_key", init=False)


@dataclass  
class TokenCredential:
    """Static bearer token credential."""
    provider: str
    token: str
    expires: Optional[int] = None  # ms since epoch
    email: Optional[str] = None
    type: str = field(default="token", init=False)


@dataclass
class OAuthCredential:
    """OAuth credential with refresh capability."""
    provider: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[int] = None  # ms since epoch
    client_id: Optional[str] = None
    email: Optional[str] = None
    type: str = field(default="oauth", init=False)


AuthProfileCredential = Union[ApiKeyCredential, TokenCredential, OAuthCredential]


@dataclass
class ProfileUsageStats:
    """Usage statistics for a profile."""
    last_used: Optional[int] = None
    cooldown_until: Optional[int] = None
    error_count: int = 0
    last_failure_at: Optional[int] = None
    failure_reason: Optional[str] = None


@dataclass
class AuthProfileStore:
    """Storage for authentication profiles."""
    version: int = 1
    profiles: Dict[str, AuthProfileCredential] = field(default_factory=dict)
    order: Dict[str, List[str]] = field(default_factory=dict)  # provider -> profile IDs
    last_good: Dict[str, str] = field(default_factory=dict)  # provider -> last successful profile
    usage_stats: Dict[str, ProfileUsageStats] = field(default_factory=dict)


# ============================================================================
# Built-in Provider Definitions
# ============================================================================

BUILTIN_PROVIDERS: Dict[str, ProviderConfig] = {
    "openai": ProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        api=ModelApi.OPENAI_COMPLETIONS,
        models=[
            ModelDefinition(
                id="gpt-4-turbo-preview",
                name="GPT-4 Turbo Preview",
                context_window=128000,
                max_tokens=4096,
                cost=ModelCost(input=10, output=30, cache_read=2.5, cache_write=10),
            ),
            ModelDefinition(
                id="gpt-4o",
                name="GPT-4o",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=128000,
                max_tokens=16384,
                cost=ModelCost(input=2.5, output=10, cache_read=1.25, cache_write=2.5),
            ),
            ModelDefinition(
                id="gpt-4o-mini",
                name="GPT-4o Mini",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=128000,
                max_tokens=16384,
                cost=ModelCost(input=0.15, output=0.6, cache_read=0.075, cache_write=0.15),
            ),
            ModelDefinition(
                id="gpt-3.5-turbo",
                name="GPT-3.5 Turbo",
                context_window=16385,
                max_tokens=4096,
                cost=ModelCost(input=0.5, output=1.5),
            ),
        ],
        priority=10,
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com",
        api=ModelApi.ANTHROPIC_MESSAGES,
        models=[
            ModelDefinition(
                id="claude-3-5-sonnet-20241022",
                name="Claude 3.5 Sonnet",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=200000,
                max_tokens=8192,
                cost=ModelCost(input=3, output=15, cache_read=0.3, cache_write=3.75),
            ),
            ModelDefinition(
                id="claude-3-5-haiku-20241022",
                name="Claude 3.5 Haiku",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=200000,
                max_tokens=8192,
                cost=ModelCost(input=0.8, output=4, cache_read=0.08, cache_write=1),
            ),
            ModelDefinition(
                id="claude-3-opus-20240229",
                name="Claude 3 Opus",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=200000,
                max_tokens=4096,
                cost=ModelCost(input=15, output=75, cache_read=1.5, cache_write=18.75),
            ),
        ],
        priority=20,
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api=ModelApi.OPENAI_COMPLETIONS,
        headers={"HTTP-Referer": "https://navig.run", "X-Title": "NAVIG"},
        models=[
            ModelDefinition(
                id="anthropic/claude-3.5-sonnet",
                name="Claude 3.5 Sonnet (OpenRouter)",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=200000,
                max_tokens=8192,
            ),
            ModelDefinition(
                id="openai/gpt-4o",
                name="GPT-4o (OpenRouter)",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=128000,
                max_tokens=16384,
            ),
            ModelDefinition(
                id="google/gemini-pro-1.5",
                name="Gemini Pro 1.5 (OpenRouter)",
                input=[ModelInput.TEXT, ModelInput.IMAGE],
                context_window=2000000,
                max_tokens=8192,
            ),
            ModelDefinition(
                id="meta-llama/llama-3.3-70b-instruct",
                name="Llama 3.3 70B (OpenRouter)",
                context_window=128000,
                max_tokens=8192,
            ),
            ModelDefinition(
                id="deepseek/deepseek-chat",
                name="DeepSeek Chat (OpenRouter)",
                context_window=64000,
                max_tokens=8192,
            ),
        ],
        priority=30,
    ),
    "ollama": ProviderConfig(
        name="ollama",
        base_url="http://127.0.0.1:11434/v1",
        api=ModelApi.OPENAI_COMPLETIONS,
        auth_header=False,  # Ollama doesn't need auth
        models=[],  # Discovered dynamically
        priority=50,
    ),
    "groq": ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api=ModelApi.OPENAI_COMPLETIONS,
        models=[
            ModelDefinition(
                id="llama-3.3-70b-versatile",
                name="Llama 3.3 70B (Groq)",
                context_window=128000,
                max_tokens=8192,
            ),
            ModelDefinition(
                id="mixtral-8x7b-32768",
                name="Mixtral 8x7B (Groq)",
                context_window=32768,
                max_tokens=8192,
            ),
        ],
        priority=40,
    ),
    "airllm": ProviderConfig(
        name="airllm",
        base_url="local://airllm",  # Local inference, no URL needed
        api=ModelApi.OPENAI_COMPLETIONS,
        auth_header=False,  # No authentication needed for local models
        models=[
            # Suggested models - users can specify any HuggingFace model
            ModelDefinition(
                id="meta-llama/Llama-3.3-70B-Instruct",
                name="Llama 3.3 70B (AirLLM)",
                context_window=128000,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),  # Free (local)
            ),
            ModelDefinition(
                id="Qwen/Qwen2.5-72B-Instruct",
                name="Qwen 2.5 72B (AirLLM)",
                context_window=32768,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),
            ),
            ModelDefinition(
                id="deepseek-ai/deepseek-coder-33b-instruct",
                name="DeepSeek Coder 33B (AirLLM)",
                context_window=16384,
                max_tokens=4096,
                cost=ModelCost(input=0, output=0),
            ),
        ],
        priority=60,  # Lower priority than cloud providers by default
    ),
}


# Environment variable mapping for providers
PROVIDER_ENV_VARS: Dict[str, List[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "groq": ["GROQ_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "xai": ["XAI_API_KEY"],
    "cerebras": ["CEREBRAS_API_KEY"],
    # AirLLM uses config-based settings, not API keys
    "airllm": ["AIRLLM_MODEL_PATH"],  # Path indicates configuration
}
