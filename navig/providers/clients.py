"""
NAVIG AI Providers - Client Implementations

Provider-specific adapters with unified interface.
Based on multi-provider architecture.
"""
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, AsyncIterator, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    pass

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    HTTPX_AVAILABLE = False

from .types import (
    BUILTIN_PROVIDERS,
    ModelApi,
    ModelDefinition,
    ProviderConfig,
)


@dataclass
class Message:
    """A chat message."""
    role: str  # "system", "user", "assistant", "tool"
    content: str
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None


@dataclass
class ToolDefinition:
    """A function/tool definition for function calling."""
    name: str
    description: str
    parameters: Dict[str, Any]
    
    def to_openai_format(self) -> Dict[str, Any]:
        """Convert to OpenAI function format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            }
        }
    
    def to_anthropic_format(self) -> Dict[str, Any]:
        """Convert to Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class CompletionRequest:
    """Request for chat completion."""
    messages: List[Message]
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: Optional[List[ToolDefinition]] = None
    tool_choice: Optional[str] = None  # "auto", "none", or specific tool name
    stream: bool = False
    stop: Optional[List[str]] = None


@dataclass
class ToolCall:
    """A tool call in a completion response."""
    id: str
    name: str
    arguments: str  # JSON string


@dataclass 
class CompletionResponse:
    """Response from chat completion."""
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    
    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ProviderError(Exception):
    """Error from a provider."""
    message: str
    status_code: Optional[int] = None
    error_type: Optional[str] = None  # "auth", "rate_limit", "billing", "invalid_request"
    provider: Optional[str] = None
    retryable: bool = False
    
    def __str__(self):
        return f"[{self.provider}] {self.message} (status={self.status_code})"


class BaseProviderClient(ABC):
    """Abstract base class for provider clients."""
    
    def __init__(
        self,
        config: ProviderConfig,
        api_key: Optional[str] = None,
        timeout: float = 60.0,
    ):
        self.config = config
        self.api_key = api_key
        self.timeout = timeout
        self._client = None  # Optional[httpx.AsyncClient]
    
    @property
    def name(self) -> str:
        return self.config.name
    
    @property
    def base_url(self) -> str:
        return self.config.base_url.rstrip("/")
    
    async def _get_client(self):
        """Get or create HTTP client."""
        if not HTTPX_AVAILABLE:
            raise ImportError("httpx is required for provider clients. Install: pip install httpx")
        
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout),
                headers=self._build_headers(),
            )
        return self._client
    
    def _build_headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {
            "Content-Type": "application/json",
            **self.config.headers,
        }
        
        if self.api_key and self.config.auth_header:
            headers["Authorization"] = f"Bearer {self.api_key}"
        
        return headers
    
    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute a chat completion request."""
        pass
    
    async def complete_stream(
        self, request: CompletionRequest
    ) -> AsyncIterator[CompletionResponse]:
        """Execute a streaming chat completion request."""
        # Default implementation: fall back to non-streaming
        request.stream = False
        result = await self.complete(request)
        yield result
    
    def get_available_models(self) -> List[ModelDefinition]:
        """Get list of available models for this provider."""
        return self.config.models
    
    def _parse_error(self, status_code: int, response_body: str) -> ProviderError:
        """Parse error response into ProviderError."""
        try:
            data = json.loads(response_body)
            if isinstance(data, list) and len(data) > 0:
                data = data[0]
            if isinstance(data, dict):
                message = data.get("error", {}).get("message", response_body)
                error_type = data.get("error", {}).get("type")
            else:
                message = response_body
                error_type = None
        except (json.JSONDecodeError, AttributeError, TypeError):
            message = response_body
            error_type = None
        
        # Determine if retryable and classify error
        retryable = status_code in (429, 500, 502, 503, 504)
        
        if status_code == 401:
            error_type = "auth"
        elif status_code == 429:
            error_type = "rate_limit"
            retryable = True
        elif status_code == 402 or "billing" in message.lower():
            error_type = "billing"
        elif status_code >= 500:
            error_type = "server_error"
            retryable = True
        
        return ProviderError(
            message=message,
            status_code=status_code,
            error_type=error_type,
            provider=self.name,
            retryable=retryable,
        )


class OpenAIClient(BaseProviderClient):
    """Client for OpenAI and OpenAI-compatible APIs."""
    
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute chat completion using OpenAI API."""
        client = await self._get_client()
        
        # Build request body
        body: Dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": m.role, "content": m.content}
                for m in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": request.stream,
        }
        
        if request.tools:
            body["tools"] = [t.to_openai_format() for t in request.tools]
            if request.tool_choice:
                body["tool_choice"] = request.tool_choice
        
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
            
            # Parse tool calls
            tool_calls = None
            if message.get("tool_calls"):
                tool_calls = [
                    ToolCall(
                        id=tc.get("id", ""),
                        name=tc.get("function", {}).get("name", ""),
                        arguments=tc.get("function", {}).get("arguments", "{}"),
                    )
                    for tc in message.get("tool_calls", [])
                ]
            
            return CompletionResponse(
                content=message.get("content"),
                tool_calls=tool_calls,
                finish_reason=choice.get("finish_reason"),
                usage=data.get("usage"),
                model=data.get("model"),
                provider=self.name,
            )
            
        except httpx.HTTPError as e:
            raise ProviderError(
                message=str(e),
                provider=self.name,
                retryable=True,
            )


class AnthropicClient(BaseProviderClient):
    """Client for Anthropic Claude API."""
    
    def _build_headers(self) -> Dict[str, str]:
        """Build Anthropic-specific headers."""
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            **self.config.headers,
        }
        
        if self.api_key:
            headers["x-api-key"] = self.api_key
        
        return headers
    
    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute chat completion using Anthropic API."""
        client = await self._get_client()
        
        # Separate system message
        system_content = None
        messages = []
        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            else:
                messages.append({"role": m.role, "content": m.content})
        
        # Build request body
        body: Dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
        }
        
        if system_content:
            body["system"] = system_content
        
        if request.tools:
            body["tools"] = [t.to_anthropic_format() for t in request.tools]
            if request.tool_choice:
                if request.tool_choice == "auto":
                    body["tool_choice"] = {"type": "auto"}
                elif request.tool_choice == "none":
                    body["tool_choice"] = {"type": "none"}
                else:
                    body["tool_choice"] = {"type": "tool", "name": request.tool_choice}
        
        if request.stop:
            body["stop_sequences"] = request.stop
        
        try:
            response = await client.post(
                f"{self.base_url}/v1/messages",
                json=body,
            )
            
            if response.status_code != 200:
                raise self._parse_error(response.status_code, response.text)
            
            data = response.json()
            
            # Parse content blocks
            content_text = None
            tool_calls = []
            
            for block in data.get("content", []):
                if block.get("type") == "text":
                    content_text = block.get("text")
                elif block.get("type") == "tool_use":
                    tool_calls.append(ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=json.dumps(block.get("input", {})),
                    ))
            
            return CompletionResponse(
                content=content_text,
                tool_calls=tool_calls if tool_calls else None,
                finish_reason=data.get("stop_reason"),
                usage={
                    "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                    "total_tokens": (
                        data.get("usage", {}).get("input_tokens", 0) +
                        data.get("usage", {}).get("output_tokens", 0)
                    ),
                },
                model=data.get("model"),
                provider=self.name,
            )
            
        except httpx.HTTPError as e:
            raise ProviderError(
                message=str(e),
                provider=self.name,
                retryable=True,
            )


# Client factory mapping
CLIENT_CLASSES: Dict[ModelApi, type] = {
    ModelApi.OPENAI_COMPLETIONS: OpenAIClient,
    ModelApi.OPENAI_RESPONSES: OpenAIClient,
    ModelApi.ANTHROPIC_MESSAGES: AnthropicClient,
}


def create_client(
    config: ProviderConfig,
    api_key: Optional[str] = None,
    timeout: float = 60.0,
    airllm_config: Optional[Any] = None,
) -> BaseProviderClient:
    """
    Create a provider client based on configuration.
    
    Args:
        config: Provider configuration
        api_key: Optional API key (overrides config)
        timeout: Request timeout in seconds
        airllm_config: Optional AirLLM configuration (for airllm provider)
    
    Returns:
        Configured provider client
    """
    # Special handling for AirLLM provider
    if config.name.lower() == "airllm":
        from .airllm import AirLLMClient, AirLLMConfig
        
        if airllm_config is None:
            airllm_config = AirLLMConfig.from_env()
        elif isinstance(airllm_config, dict):
            airllm_config = AirLLMConfig.from_dict(airllm_config)
        
        return AirLLMClient(
            config=config,
            airllm_config=airllm_config,
            timeout=timeout,
        )
    
    client_class = CLIENT_CLASSES.get(config.api, OpenAIClient)
    return client_class(config, api_key=api_key, timeout=timeout)


def get_builtin_provider(name: str) -> Optional[ProviderConfig]:
    """Get a built-in provider configuration by name."""
    return BUILTIN_PROVIDERS.get(name.lower())
