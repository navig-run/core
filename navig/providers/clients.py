"""
NAVIG AI Providers - Client Implementations

Provider-specific adapters with unified interface.
Based on multi-provider architecture.
"""

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    httpx = None
    HTTPX_AVAILABLE = False

from .types import BUILTIN_PROVIDERS, ModelApi, ModelDefinition, ProviderConfig


@dataclass
class Message:
    """A chat message."""

    role: str  # "system", "user", "assistant", "tool"
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None


@dataclass
class ToolDefinition:
    """A function/tool definition for function calling."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_openai_format(self) -> dict[str, Any]:
        """Convert to OpenAI function format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def to_anthropic_format(self) -> dict[str, Any]:
        """Convert to Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


@dataclass
class CompletionRequest:
    """Request for chat completion."""

    messages: list[Message]
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    tools: list[ToolDefinition] | None = None
    tool_choice: str | None = None  # "auto", "none", or specific tool name
    stream: bool = False
    stop: list[str] | None = None
    extra_body: dict | None = None  # Provider-specific params (thinking budgets, etc.)


@dataclass
class ToolCall:
    """A tool call in a completion response."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class CompletionResponse:
    """Response from chat completion."""

    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    model: str | None = None
    provider: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


@dataclass
class ProviderError(Exception):
    """Error from a provider."""

    message: str
    status_code: int | None = None
    error_type: str | None = None  # "auth", "rate_limit", "billing", "invalid_request"
    provider: str | None = None
    retryable: bool = False

    def __str__(self):
        return f"[{self.provider}] {self.message} (status={self.status_code})"


@dataclass
class StreamChunk:
    """A single chunk from a streaming completion response.

    When the stream starts, the first chunk often contains just the
    ``model`` and ``provider`` fields.  Subsequent chunks carry ``delta``
    (text token) or ``tool_call_delta`` fragments.  The final chunk sets
    ``finish_reason``.
    """

    delta: str | None = None
    tool_call_delta: ToolCall | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    model: str | None = None
    provider: str | None = None


class BaseProviderClient(ABC):
    """Abstract base class for provider clients."""

    def __init__(
        self,
        config: ProviderConfig,
        api_key: str | None = None,
        timeout: float = 60.0,
    ):
        self.config = config
        self.api_key = api_key
        self.timeout = timeout
        self._client = None  # httpx.AsyncClient | None

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

    def _build_headers(self) -> dict[str, str]:
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

    async def complete_stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """Execute a streaming chat completion request.

        Default: falls back to non-streaming and yields a single chunk.
        Override in subclasses for true SSE streaming.
        """
        request.stream = False
        result = await self.complete(request)
        yield StreamChunk(
            delta=result.content,
            finish_reason=result.finish_reason,
            usage=result.usage,
            model=result.model,
            provider=result.provider,
        )

    def get_available_models(self) -> list[ModelDefinition]:
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
        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
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
            ) from e

    async def complete_stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """Stream chat completions via OpenAI-compatible SSE."""
        client = await self._get_client()

        body: dict[str, Any] = {
            "model": request.model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
        }

        if request.tools:
            body["tools"] = [t.to_openai_format() for t in request.tools]
            if request.tool_choice:
                body["tool_choice"] = request.tool_choice

        if request.stop:
            body["stop"] = request.stop

        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=body,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise self._parse_error(response.status_code, error_body.decode())

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]  # strip "data: "
                    if payload == "[DONE]":
                        break
                    try:
                        chunk_data = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    choice = (chunk_data.get("choices") or [{}])[0]
                    delta = choice.get("delta", {})

                    # Text delta
                    text = delta.get("content")

                    # Tool call delta
                    tc_delta = None
                    if delta.get("tool_calls"):
                        tc = delta["tool_calls"][0]
                        tc_delta = ToolCall(
                            id=tc.get("id", ""),
                            name=tc.get("function", {}).get("name", ""),
                            arguments=tc.get("function", {}).get("arguments", ""),
                        )

                    yield StreamChunk(
                        delta=text,
                        tool_call_delta=tc_delta,
                        finish_reason=choice.get("finish_reason"),
                        usage=chunk_data.get("usage"),
                        model=chunk_data.get("model"),
                        provider=self.name,
                    )

        except httpx.HTTPError as e:
            raise ProviderError(
                message=str(e),
                provider=self.name,
                retryable=True,
            ) from e


class AnthropicClient(BaseProviderClient):
    """Client for Anthropic Claude API."""

    def _build_headers(self) -> dict[str, str]:
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
        body: dict[str, Any] = {
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
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=json.dumps(block.get("input", {})),
                        )
                    )

            return CompletionResponse(
                content=content_text,
                tool_calls=tool_calls if tool_calls else None,
                finish_reason=data.get("stop_reason"),
                usage={
                    "prompt_tokens": data.get("usage", {}).get("input_tokens", 0),
                    "completion_tokens": data.get("usage", {}).get("output_tokens", 0),
                    "total_tokens": (
                        data.get("usage", {}).get("input_tokens", 0)
                        + data.get("usage", {}).get("output_tokens", 0)
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
            ) from e

    async def complete_stream(self, request: CompletionRequest) -> AsyncIterator[StreamChunk]:
        """Stream chat completions via Anthropic SSE."""
        client = await self._get_client()

        system_content = None
        messages = []
        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            else:
                messages.append({"role": m.role, "content": m.content})

        body: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_tokens": request.max_tokens,
            "stream": True,
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
            async with client.stream(
                "POST",
                f"{self.base_url}/v1/messages",
                json=body,
            ) as response:
                if response.status_code != 200:
                    error_body = await response.aread()
                    raise self._parse_error(response.status_code, error_body.decode())

                current_tool_id = ""
                current_tool_name = ""

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    if event_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            current_tool_id = block.get("id", "")
                            current_tool_name = block.get("name", "")

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta":
                            yield StreamChunk(
                                delta=delta.get("text"),
                                provider=self.name,
                            )
                        elif delta_type == "input_json_delta":
                            yield StreamChunk(
                                tool_call_delta=ToolCall(
                                    id=current_tool_id,
                                    name=current_tool_name,
                                    arguments=delta.get("partial_json", ""),
                                ),
                                provider=self.name,
                            )

                    elif event_type == "message_delta":
                        yield StreamChunk(
                            finish_reason=event.get("delta", {}).get("stop_reason"),
                            usage={
                                "completion_tokens": event.get("usage", {}).get("output_tokens", 0),
                            },
                            provider=self.name,
                        )

                    elif event_type == "message_start":
                        msg = event.get("message", {})
                        yield StreamChunk(
                            model=msg.get("model"),
                            provider=self.name,
                        )

        except httpx.HTTPError as e:
            raise ProviderError(
                message=str(e),
                provider=self.name,
                retryable=True,
            ) from e


# Client factory mapping
CLIENT_CLASSES: dict[ModelApi, type] = {
    ModelApi.OPENAI_COMPLETIONS: OpenAIClient,
    ModelApi.OPENAI_RESPONSES: OpenAIClient,
    ModelApi.ANTHROPIC_MESSAGES: AnthropicClient,
}


def create_client(
    config: ProviderConfig,
    api_key: str | None = None,
    timeout: float = 60.0,
    airllm_config: Any | None = None,
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


def get_builtin_provider(name: str) -> ProviderConfig | None:
    """Get a built-in provider configuration by name."""
    return BUILTIN_PROVIDERS.get(name.lower())
