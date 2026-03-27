"""
NAVIG AI Providers - Perplexity Client

Perplexity Sonar API for real-time web search with AI synthesis.
Supports both direct API and OpenRouter proxy.

Based on standard perplexity integration pattern.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    import httpx

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None  # type: ignore[assignment]
    HTTPX_AVAILABLE = False

from .clients import (
    BaseProviderClient,
    CompletionRequest,
    CompletionResponse,
    ProviderError,
)
from .types import ModelApi, ModelDefinition, ProviderConfig

# API Endpoints
PERPLEXITY_DIRECT_URL = "https://api.perplexity.ai"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

# API Key Prefixes for auto-detection
PERPLEXITY_KEY_PREFIX = "pplx-"
OPENROUTER_KEY_PREFIX = "sk-or-"

# Default models
DEFAULT_PERPLEXITY_MODEL = "sonar-pro"
PERPLEXITY_MODELS = [
    ModelDefinition(
        id="sonar",
        name="Sonar",
        context_window=127000,
        max_tokens=4096,
    ),
    ModelDefinition(
        id="sonar-pro",
        name="Sonar Pro",
        context_window=200000,
        max_tokens=8192,
    ),
    ModelDefinition(
        id="sonar-reasoning",
        name="Sonar Reasoning",
        context_window=127000,
        max_tokens=4096,
        reasoning=True,
    ),
]


@dataclass
class PerplexitySearchResult:
    """A search result from Perplexity API."""

    content: str
    citations: List[str]
    model: str
    usage: Optional[Dict[str, int]] = None


class PerplexityClient(BaseProviderClient):
    """
    Client for Perplexity Sonar API.

    Supports:
    - Direct Perplexity API (pplx-xxx keys)
    - OpenRouter proxy (sk-or-xxx keys)
    - Auto-detection of API type from key prefix
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: str = DEFAULT_PERPLEXITY_MODEL,
        timeout: float = 60.0,
    ):
        """
        Initialize Perplexity client.

        Args:
            api_key: API key (Perplexity or OpenRouter)
            base_url: Optional base URL override
            model: Model to use (sonar, sonar-pro, sonar-reasoning)
            timeout: Request timeout
        """
        # Resolve API key from env if not provided
        self.api_key = api_key or self._resolve_api_key()
        self.model = model
        self.timeout = timeout

        # Determine base URL from key type
        if base_url:
            self._base_url = base_url
        else:
            self._base_url = self._detect_base_url()

        self._client = None

        # Create a minimal config for parent class
        self.config = ProviderConfig(
            name="perplexity",
            base_url=self._base_url,
            api=ModelApi.OPENAI_COMPLETIONS,
            models=PERPLEXITY_MODELS,
            env_key="PERPLEXITY_API_KEY",
        )

    def _resolve_api_key(self) -> Optional[str]:
        """Resolve API key from environment."""
        # Try Perplexity key first
        key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
        if key:
            return key

        # Fall back to OpenRouter
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if key:
            return key

        return None

    def _detect_base_url(self) -> str:
        """Detect base URL from API key prefix."""
        if not self.api_key:
            return PERPLEXITY_DIRECT_URL

        key_lower = self.api_key.lower()

        if key_lower.startswith(PERPLEXITY_KEY_PREFIX):
            return PERPLEXITY_DIRECT_URL
        elif key_lower.startswith(OPENROUTER_KEY_PREFIX):
            return OPENROUTER_URL

        # Default to direct API
        return PERPLEXITY_DIRECT_URL

    @property
    def is_openrouter(self) -> bool:
        """Check if using OpenRouter proxy."""
        return OPENROUTER_URL in self._base_url

    @property
    def base_url(self) -> str:
        return self._base_url

    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # OpenRouter requires additional headers
        if self.is_openrouter:
            headers["HTTP-Referer"] = "https://navig.run"
            headers["X-Title"] = "NAVIG"

        return headers

    async def _get_client(self):
        """Get or create HTTP client."""
        if not HTTPX_AVAILABLE:
            raise ImportError(
                "httpx is required for Perplexity client. Install: pip install httpx"
            )

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers=self._build_headers(),
            )
        return self._client

    async def search(
        self,
        query: str,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
        max_tokens: int = 4096,
    ) -> PerplexitySearchResult:
        """
        Perform a web search with AI synthesis.

        Args:
            query: Search query
            model: Optional model override
            system_prompt: Optional system prompt
            max_tokens: Maximum response tokens

        Returns:
            PerplexitySearchResult with content and citations
        """
        client = await self._get_client()

        model_id = model or self.model

        # Adjust model ID for OpenRouter
        if self.is_openrouter and not model_id.startswith("perplexity/"):
            model_id = f"perplexity/{model_id}"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": query})

        body = {
            "model": model_id,
            "messages": messages,
            "max_tokens": max_tokens,
        }

        # Perplexity-specific: request citations
        if not self.is_openrouter:
            body["return_citations"] = True
            body["return_related_questions"] = False

        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
            )

            if response.status_code != 200:
                raise self._parse_error(response.status_code, response.text)

            data = response.json()
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})

            # Extract citations
            citations = data.get("citations", [])

            return PerplexitySearchResult(
                content=message.get("content", ""),
                citations=citations,
                model=data.get("model", model_id),
                usage=data.get("usage"),
            )

        except httpx.HTTPError as e:
            raise ProviderError(
                message=str(e),
                provider="perplexity",
                retryable=True,
            ) from e

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """
        Execute chat completion using Perplexity API.

        Note: Perplexity doesn't support tools/function calling.
        Use search() for web search queries.
        """
        client = await self._get_client()

        model_id = request.model or self.model

        # Adjust model ID for OpenRouter
        if self.is_openrouter and not model_id.startswith("perplexity/"):
            model_id = f"perplexity/{model_id}"

        body: Dict[str, Any] = {
            "model": model_id,
            "messages": [
                {"role": m.role, "content": m.content} for m in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        if request.stop:
            body["stop"] = request.stop

        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
            )

            if response.status_code != 200:
                raise self._parse_error(response.status_code, response.text)

            data = response.json()
            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})

            return CompletionResponse(
                content=message.get("content"),
                tool_calls=None,  # Perplexity doesn't support tools
                finish_reason=choice.get("finish_reason"),
                usage=data.get("usage"),
                model=data.get("model"),
                provider="perplexity",
            )

        except httpx.HTTPError as e:
            raise ProviderError(
                message=str(e),
                provider="perplexity",
                retryable=True,
            ) from e

    def _parse_error(self, status_code: int, response_body: str) -> ProviderError:
        """Parse error response into ProviderError."""
        try:
            data = json.loads(response_body)
            message = data.get("error", {}).get("message", response_body)
        except json.JSONDecodeError:
            message = response_body

        error_type = None
        retryable = status_code in (429, 500, 502, 503, 504)

        if status_code == 401:
            error_type = "auth"
        elif status_code == 429:
            error_type = "rate_limit"
            retryable = True

        return ProviderError(
            message=message,
            status_code=status_code,
            error_type=error_type,
            provider="perplexity",
            retryable=retryable,
        )


# Convenience functions


def create_perplexity_client(
    api_key: Optional[str] = None,
    model: str = DEFAULT_PERPLEXITY_MODEL,
    timeout: float = 60.0,
) -> PerplexityClient:
    """
    Create a Perplexity client.

    Args:
        api_key: API key (auto-detected from env if not provided)
        model: Model to use
        timeout: Request timeout

    Returns:
        Configured PerplexityClient
    """
    return PerplexityClient(api_key=api_key, model=model, timeout=timeout)


async def perplexity_search(
    query: str,
    api_key: Optional[str] = None,
    model: str = DEFAULT_PERPLEXITY_MODEL,
    system_prompt: Optional[str] = None,
) -> PerplexitySearchResult:
    """
    Perform a one-shot Perplexity search.

    Args:
        query: Search query
        api_key: API key (auto-detected from env if not provided)
        model: Model to use
        system_prompt: Optional system prompt

    Returns:
        PerplexitySearchResult
    """
    client = create_perplexity_client(api_key=api_key, model=model)
    try:
        return await client.search(query, system_prompt=system_prompt)
    finally:
        await client.close()


def is_perplexity_available() -> bool:
    """Check if Perplexity API is configured."""
    key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if key:
        return True

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    return bool(key)


# Provider configuration for registry
PERPLEXITY_PROVIDER = ProviderConfig(
    name="perplexity",
    base_url=PERPLEXITY_DIRECT_URL,
    api=ModelApi.OPENAI_COMPLETIONS,
    models=PERPLEXITY_MODELS,
    api_key="PERPLEXITY_API_KEY",  # Env var name
    auth_header=True,
)
