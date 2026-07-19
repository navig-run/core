"""
NAVIG AI Providers - Client Implementations

Provider-specific adapters with unified interface.
Based on multi-provider architecture.
"""

import json
import logging
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

from navig._llm_defaults import _DEFAULT_MAX_TOKENS, _DEFAULT_TEMPERATURE

from .types import BUILTIN_PROVIDERS, ModelApi, ModelDefinition, ProviderConfig

logger = logging.getLogger(__name__)


def _sanitize_openai_body(body: dict[str, Any], provider_name: str) -> dict[str, Any]:
    """Scrub an OpenAI-compatible request body for backends with strict deserializers.

    The default body our client builds is OpenAI-compliant, but several
    OpenAI-compatible providers (notably NVIDIA NIM) reject minor schema
    deviations with cryptic serde-rust errors like::

        invalid type: unit variant, expected newtype variant at line 1 column 40

    The fixes we apply here are conservative — they match the payload shape
    the official OpenAI Python SDK sends when no special parameters are set.

    Adjustments by provider:

    * **nvidia** — NIM's preprocessor balks at ``stream: false`` and at any
      message ``content`` that isn't a plain non-null string. We omit
      ``stream`` when defaulted, force-coerce content to ``str``, drop empty
      messages, and ensure ``temperature`` is a Python ``float``.
    * **other providers** — only minimal normalisation (drop ``None``s).
    """
    out = dict(body)

    # Universal: never send None values (some servers expect omission, not null).
    out = {k: v for k, v in out.items() if v is not None}

    if provider_name == "nvidia":
        # NIM rejects `stream: false` on some deployments; omit when defaulted.
        if out.get("stream") is False:
            out.pop("stream", None)
        # Drop fields that NIM rejects but the OpenAI SDK omits by default.
        out.pop("response_format", None)
        # Ensure numeric types are exactly what NIM expects.
        if "temperature" in out:
            try:
                out["temperature"] = float(out["temperature"])
            except (TypeError, ValueError):
                out.pop("temperature", None)
        if "max_tokens" in out:
            try:
                out["max_tokens"] = int(out["max_tokens"])
            except (TypeError, ValueError):
                out.pop("max_tokens", None)
        # Coerce message content to a plain string and drop empty messages.
        msgs = out.get("messages") or []
        cleaned: list[dict[str, Any]] = []
        for m in msgs:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, list):
                # Multimodal content not supported on NIM's text-only models —
                # join string parts; ignore others. Vision routes use the
                # vision-specific endpoint instead.
                text_parts = [p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                content = "".join(text_parts)
            if content is None:
                content = ""
            if not isinstance(content, str):
                content = str(content)
            if not role:
                continue
            cleaned.append({"role": role, "content": content})
        # OpenAI-compatible APIs require conversations to start with a user
        # (or system) message.  A persistent session that carries the previous
        # assistant reply as the first element causes the 500 serde error.
        # Strip any leading assistant turns so the first message is always
        # user/system — this is safe because those turns contain no new
        # information the model needs.
        while cleaned and cleaned[0].get("role") == "assistant":
            cleaned.pop(0)

        out["messages"] = cleaned

    return out


def _openai_tool_choice(tool_choice: Any) -> Any:
    """OpenAI accepts only 'auto'|'none'|'required' as a bare string; a specific
    tool NAME must be the object form. (Anthropic handles the named case itself.)"""
    if tool_choice in (None, "auto", "none", "required"):
        return tool_choice
    return {"type": "function", "function": {"name": tool_choice}}


def _openai_wire_messages(messages: list) -> list[dict[str, Any]]:
    """Serialize Message objects to OpenAI chat wire format, PRESERVING tool-call
    fields — ``assistant.tool_calls`` and ``tool.tool_call_id``/``name``. Dropping
    them 400s a multi-turn tool round-trip (a 'tool' role message must carry a
    ``tool_call_id``, and the assistant turn must carry its ``tool_calls``)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        msg: dict[str, Any] = {"role": m.role, "content": m.content}
        name = getattr(m, "name", None)
        if name:
            msg["name"] = name
        tcs = getattr(m, "tool_calls", None)
        if tcs:
            msg["tool_calls"] = tcs  # already OpenAI-shaped dicts
        tcid = getattr(m, "tool_call_id", None)
        if tcid:
            msg["tool_call_id"] = tcid
        out.append(msg)
    return out


def _anthropic_wire_messages(messages: list) -> list[dict[str, Any]]:
    """Translate non-system Message objects to Anthropic's message format,
    including tool calling: an assistant turn's ``tool_calls`` become ``tool_use``
    content blocks; a ``role='tool'`` result becomes a ``tool_result`` block inside
    a ``user`` message (merged with adjacent tool results — Anthropic requires all
    results for a turn in a single user message and rejects a bare ``tool`` role)."""
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.role
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": getattr(m, "tool_call_id", "") or "",
                "content": m.content or "",
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
            continue
        tcs = getattr(m, "tool_calls", None)
        if role == "assistant" and tcs:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in tcs:
                fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                raw_args = fn.get("arguments") if fn else None
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
                except (json.JSONDecodeError, TypeError):
                    args = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", "") if isinstance(tc, dict) else "",
                    "name": fn.get("name", "") if fn else "",
                    "input": args,
                })
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": role, "content": m.content})
    return out


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
    temperature: float = _DEFAULT_TEMPERATURE
    max_tokens: int = _DEFAULT_MAX_TOKENS
    tools: list[ToolDefinition] | None = None
    tool_choice: str | None = None  # "auto", "none", or specific tool name
    stream: bool = False
    stop: list[str] | None = None
    extra_body: dict | None = None  # Provider-specific params (thinking budgets, etc.)
    cache_control: bool = False  # F-12: inject Anthropic prompt-caching markers


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
    cache_read_input_tokens: int = 0   # F-12: Anthropic prompt-cache read tokens
    cache_creation_input_tokens: int = 0  # F-12: Anthropic prompt-cache write tokens
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
        oauth_token: str | None = None,
    ):
        self.config = config
        self.api_key = api_key
        # A Claude.ai subscription OAuth token (Bearer). Mutually exclusive with
        # api_key on Anthropic — see anthropic_oauth for why it must present as
        # the official CLI.
        self.oauth_token = oauth_token
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

        # A provider can send {"error":{"message":null}} → message is non-str;
        # coerce before `.lower()` below (else AttributeError masks the real error).
        if not isinstance(message, str):
            message = str(message) if message is not None else response_body

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
            "messages": _openai_wire_messages(request.messages),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": request.stream,
        }

        if request.tools:
            body["tools"] = [t.to_openai_format() for t in request.tools]
            if request.tool_choice:
                body["tool_choice"] = _openai_tool_choice(request.tool_choice)

        if request.stop:
            body["stop"] = request.stop

        # Provider-specific scrubbing — some OpenAI-compatible backends (notably
        # NVIDIA NIM) have stricter serde-based deserializers that choke on
        # fields the official OpenAI SDK omits when defaulted. Sanitize here so
        # we always send a request shape known to work.
        body = _sanitize_openai_body(body, provider_name=self.name)

        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                json=body,
            )

            if response.status_code != 200:
                # Log the payload (lightly redacted) so we can diagnose strict
                # backends. Truncate to avoid flooding logs on big prompts.
                import json as _json
                try:
                    body_preview = _json.dumps(body, ensure_ascii=False)[:600]
                except Exception:
                    body_preview = "<unserializable>"
                logger.warning(
                    "Provider %s returned HTTP %d. Sent body: %s",
                    self.name, response.status_code, body_preview,
                )
                raise self._parse_error(response.status_code, response.text)

            data = response.json()
            # `choices` can be present-but-empty (content filter / some compatible
            # backends) — `.get(..., [{}])[0]` would IndexError on []; guard with `or`.
            choice = (data.get("choices") or [{}])[0]
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
            "messages": _openai_wire_messages(request.messages),
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
            # Ask OpenAI-compatible backends (incl. xAI/grok) to emit a final
            # usage chunk. Without this, SSE returns no usage at all and every
            # streamed turn records 0 tokens / $0.00. The stream loop already
            # captures chunk.usage when present.
            "stream_options": {"include_usage": True},
        }

        if request.tools:
            body["tools"] = [t.to_openai_format() for t in request.tools]
            if request.tool_choice:
                body["tool_choice"] = _openai_tool_choice(request.tool_choice)

        if request.stop:
            body["stop"] = request.stop

        # Same provider-specific scrubbing as complete() — NVIDIA NIM's strict
        # deserializer rejects the same fields on the streaming endpoint too.
        body = _sanitize_openai_body(body, provider_name=self.name)

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
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].lstrip()  # tolerate "data:" and "data: "  # strip "data: "
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

    @staticmethod
    def _apply_cache_control(
        system_content: Any, messages: list[dict]
    ) -> Any:
        """Inject Anthropic prompt-cache markers on system + first 2 user messages.

        Prompt caching is GA (no beta header). A breakpoint on the system block
        caches tools+system together (render order is tools → system → messages),
        which is the bulk of a ReAct turn's frozen prefix. Mutates *messages* in
        place and returns the (possibly rewrapped) system content. Shared by
        ``complete()`` and ``complete_stream()`` so streamed turns cache too.
        """
        if system_content is not None and isinstance(system_content, str):
            system_content = [
                {"type": "text", "text": system_content, "cache_control": {"type": "ephemeral"}}
            ]
        user_count = 0
        for msg in messages:
            if msg["role"] == "user" and user_count < 2 and isinstance(msg["content"], str):
                msg["content"] = [
                    {"type": "text", "text": msg["content"], "cache_control": {"type": "ephemeral"}}
                ]
                user_count += 1
        return system_content

    def _build_headers(self) -> dict[str, str]:
        """Build Anthropic-specific headers.

        Two distinct auth modes:
          * OAuth (Claude.ai Pro/Max subscription): must present as the official
            Claude Code CLI (Bearer + oauth beta + claude-cli UA + x-app:cli, and
            crucially NO x-api-key). See :mod:`navig.providers.anthropic_oauth`.
          * API key: plain ``x-api-key`` + version.
        """
        if self.oauth_token:
            from navig.providers.anthropic_oauth import build_oauth_headers

            # config.headers first so OAuth-critical headers always win.
            return {**self.config.headers, **build_oauth_headers(self.oauth_token)}

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            **self.config.headers,
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    def _frame_system(self, system_content: Any) -> Any:
        """In OAuth mode the system MUST be an array led by the Claude Code block."""
        if self.oauth_token:
            from navig.providers.anthropic_oauth import frame_oauth_system

            return frame_oauth_system(system_content)
        return system_content

    async def _post_with_retry(self, client, body: dict[str, Any]):
        """POST /v1/messages, retrying 429/529/5xx with backoff that honors
        Retry-After. Returns the 200 response or raises a ProviderError."""
        import asyncio

        from navig.providers.anthropic_oauth import (
            MAX_RETRIES,
            compute_backoff,
            is_retryable_status,
            parse_retry_after,
        )

        last = None
        for attempt in range(MAX_RETRIES + 1):
            response = await client.post(f"{self.base_url}/v1/messages", json=body)
            if response.status_code == 200:
                return response
            last = response
            # A SUBSCRIPTION/account rate-limit won't clear in seconds — retrying
            # 5× with backoff just looks like a hang. Fail FAST with the clear
            # message so the operator sees "rate limited" instead of a stall.
            # (A generic/transient 429 without the account wording still retries.)
            if response.status_code == 429:
                _b = (response.text or "").lower()
                if "account" in _b and "rate limit" in _b:
                    raise self._parse_error(response.status_code, response.text)
            if is_retryable_status(response.status_code) and attempt < MAX_RETRIES:
                ra = parse_retry_after(response.headers.get("retry-after"))
                await asyncio.sleep(compute_backoff(attempt, ra))
                continue
            raise self._parse_error(response.status_code, response.text)
        raise self._parse_error(last.status_code, last.text)

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Execute chat completion using Anthropic API."""
        client = await self._get_client()

        # Separate system message
        system_content = None
        non_system = []
        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            else:
                non_system.append(m)
        # Translate to Anthropic format (tool_use / tool_result blocks).
        messages = _anthropic_wire_messages(non_system)

        # F-12: inject Anthropic prompt-caching markers on system + first 2 user messages
        if request.cache_control:
            system_content = self._apply_cache_control(system_content, messages)

        # OAuth subscription: the system MUST be an array led by the Claude Code
        # identity block (caller's own system goes second). No-op for API keys.
        system_content = self._frame_system(system_content)

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

        # Provider-specific params (adaptive thinking, output_config.effort, …).
        # These are top-level fields on the Anthropic Messages body, so a shallow
        # merge is correct. NOTE: never inject temperature/top_p/budget_tokens for
        # Opus 4.8/4.7 — they 400. The effort layer emits only thinking/output_config.
        if request.extra_body:
            body.update(request.extra_body)

        try:
            response = await self._post_with_retry(client, body)

            data = response.json()

            # Parse content blocks — concatenate ALL text blocks (Anthropic can
            # split text or emit it around tool_use blocks; keeping only the last
            # silently truncated the reply).
            text_parts: list[str] = []
            tool_calls = []

            for block in data.get("content", []):
                if block.get("type") == "text":
                    text_parts.append(block.get("text") or "")
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            arguments=json.dumps(block.get("input", {})),
                        )
                    )

            return CompletionResponse(
                content="".join(text_parts) if text_parts else None,
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
                cache_read_input_tokens=data.get("usage", {}).get("cache_read_input_tokens", 0),
                cache_creation_input_tokens=data.get("usage", {}).get("cache_creation_input_tokens", 0),
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
        non_system = []
        for m in request.messages:
            if m.role == "system":
                system_content = m.content
            else:
                non_system.append(m)
        # Translate to Anthropic format (tool_use / tool_result blocks).
        messages = _anthropic_wire_messages(non_system)

        # Same cache marker injection as complete() — streamed turns cache too.
        if request.cache_control:
            system_content = self._apply_cache_control(system_content, messages)

        # OAuth subscription: enforce the Claude Code system framing (see complete()).
        system_content = self._frame_system(system_content)

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

        if request.extra_body:
            body.update(request.extra_body)

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
                # Anthropic reports input + cache tokens in message_start.usage and
                # only output_tokens in message_delta.usage — carry the former so
                # the final usage chunk has the real prompt + cache counts (else the
                # consumer records 0 prompt / 0 cache tokens and mis-bills caching).
                _in_tokens = 0
                _cache_read = 0
                _cache_write = 0

                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].lstrip()  # tolerate "data:" and "data: "
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type", "")

                    # Anthropic can emit a mid-stream error event (rate_limit /
                    # overloaded) after a 200 was established. Surface it as a
                    # retryable ProviderError instead of silently truncating.
                    if event_type == "error":
                        from navig.providers.anthropic_oauth import sse_error

                        parsed = sse_error(event)
                        if parsed:
                            etype, emsg = parsed
                            raise ProviderError(
                                message=emsg,
                                provider=self.name,
                                error_type=etype,
                                retryable=etype in ("rate_limit", "overloaded", "overloaded_error"),
                            )

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
                                "prompt_tokens": _in_tokens,
                                "completion_tokens": event.get("usage", {}).get("output_tokens", 0),
                                "cache_read_input_tokens": _cache_read,
                                "cache_creation_input_tokens": _cache_write,
                            },
                            provider=self.name,
                        )

                    elif event_type == "message_start":
                        msg = event.get("message", {})
                        _u = msg.get("usage", {}) or {}
                        _in_tokens = _u.get("input_tokens", 0) or 0
                        _cache_read = _u.get("cache_read_input_tokens", 0) or 0
                        _cache_write = _u.get("cache_creation_input_tokens", 0) or 0
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
    oauth_token: str | None = None,
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
    return client_class(config, api_key=api_key, timeout=timeout, oauth_token=oauth_token)


def get_builtin_provider(name: str) -> ProviderConfig | None:
    """Get a built-in provider configuration by name."""
    return BUILTIN_PROVIDERS.get(name.lower())
