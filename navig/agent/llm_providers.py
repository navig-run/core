"""
NAVIG Unified LLM Provider Layer

Pluggable provider interface that abstracts Ollama, OpenRouter, OpenAI,
GitHub Models, and llama.cpp behind a single async ``chat()`` method.

Each provider is instantiated from config and used by the model router
to send requests to the correct backend regardless of where the model
lives (local or remote).

Provider selection is per-model-slot, not global:
    small  → OllamaProvider   (local, fast)
    big    → OpenRouterProvider (remote, powerful)
    coder  → OpenRouterProvider (remote, code-optimised)

No provider touches routing logic — that belongs to model_router.py.
"""

from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Lazy aiohttp import
_aiohttp = None


async def _get_aiohttp():
    global _aiohttp
    if _aiohttp is None:
        import aiohttp
        _aiohttp = aiohttp
    return _aiohttp


# ── Response wrapper ────────────────────────────────────────────────

@dataclass
class LLMResponse:
    """Unified response from any provider."""
    content: str
    model: str = ""
    provider: str = ""
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


# ── Base class ──────────────────────────────────────────────────────

class LLMProvider:
    """Abstract base for all LLM providers."""

    name: str = "base"

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        self.base_url = base_url
        self.api_key = api_key
        self._session = None

    async def _get_session(self):
        aio = await _get_aiohttp()
        if self._session is None or self._session.closed:
            self._session = aio.ClientSession()
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
        **kwargs,
    ) -> LLMResponse:
        raise NotImplementedError(
            f"{self.__class__.__name__}.chat() is not implemented. "
            "Use a concrete provider (ollama, openrouter, openai, llamacpp)."
        )

    async def is_available(self) -> bool:
        """Quick health check (best-effort)."""
        return False

    async def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ):
        """
        Async generator yielding text chunks as they arrive.

        Default implementation falls back to buffered ``chat()`` and
        yields the full response as a single chunk.
        """
        resp = await self.chat(model, messages, temperature, max_tokens, **kwargs)
        yield resp.content

    def __repr__(self):
        return f"<{self.__class__.__name__} url={self.base_url!r}>"


# ── Ollama ──────────────────────────────────────────────────────────

class OllamaProvider(LLMProvider):
    """Local Ollama via OpenAI-compatible /v1 endpoint."""

    name = "ollama"

    def __init__(self, base_url: str = "", **kwargs):
        super().__init__(base_url=base_url or "http://localhost:11434", **kwargs)

    async def chat(self, model, messages, temperature=0.7, max_tokens=512, **kw):
        session = await self._get_session()
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if "num_ctx" in kw:
            payload["num_ctx"] = kw["num_ctx"]

        t0 = time.monotonic()
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Ollama error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model),
            provider=self.name,
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )

    async def is_available(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/api/tags", timeout=_timeout(3)) as r:
                return r.status == 200
        except Exception:
            return False


# ── OpenRouter ──────────────────────────────────────────────────────

class OpenRouterProvider(LLMProvider):
    """OpenRouter.ai — OpenAI-compatible remote gateway."""

    name = "openrouter"

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        super().__init__(
            base_url=base_url or "https://openrouter.ai/api/v1",
            api_key=api_key or os.getenv("OPENROUTER_API_KEY", ""),
            **kwargs,
        )

    async def chat(self, model, messages, temperature=0.7, max_tokens=512, **kw):
        if not self.api_key:
            raise RuntimeError("OpenRouter API key not set")

        session = await self._get_session()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://navig.run",
            "X-Title": "NAVIG",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.monotonic()
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenRouter error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model),
            provider=self.name,
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )

    async def is_available(self) -> bool:
        return bool(self.api_key)


# ── OpenAI-compatible ───────────────────────────────────────────────

class OpenAIProvider(LLMProvider):
    """Any OpenAI-compatible endpoint (OpenAI, Azure, self-hosted)."""

    name = "openai"

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        super().__init__(
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key or os.getenv("OPENAI_API_KEY", ""),
            **kwargs,
        )

    async def chat(self, model, messages, temperature=0.7, max_tokens=512, **kw):
        if not self.api_key:
            raise RuntimeError("OpenAI API key not set")

        session = await self._get_session()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.monotonic()
        async with session.post(url, headers=headers, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"OpenAI error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model),
            provider=self.name,
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )

    async def is_available(self) -> bool:
        return bool(self.api_key)


# ── llama.cpp HTTP server ───────────────────────────────────────────

class LlamaCppProvider(LLMProvider):
    """llama.cpp server (--server mode), OpenAI-compatible."""

    name = "llamacpp"

    def __init__(self, base_url: str = "", **kwargs):
        super().__init__(base_url=base_url or "http://localhost:8080", **kwargs)

    async def chat(self, model, messages, temperature=0.7, max_tokens=512, **kw):
        session = await self._get_session()
        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.monotonic()
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"llama.cpp error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model),
            provider=self.name,
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )

    async def is_available(self) -> bool:
        try:
            session = await self._get_session()
            async with session.get(f"{self.base_url}/health", timeout=_timeout(3)) as r:
                return r.status == 200
        except Exception:
            return False


# ── MCP Forge (VS Code Copilot via MCP WebSocket) ──────────────────

class McpForgeProvider(LLMProvider):
    """
    LLM provider that calls VS Code Copilot via MCP protocol (WebSocket).

    The navig-bridge VS Code extension runs an MCP server on port 42070
    that exposes ``vscode_llm_chat`` as a standard MCP tool.  This provider
    connects as an MCP client and invokes that tool for inference.

    Config in ~/.navig/config.yaml::

        forge:
          mcp_url: ws://127.0.0.1:42070
          token: <shared-secret>
    """

    name = "mcp_forge"
    DEFAULT_URL = "ws://127.0.0.1:42070"

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        super().__init__(base_url=base_url or self.DEFAULT_URL, api_key=api_key, **kwargs)
        self._ws = None
        self._request_id = 0
        self._initialized = False

    def _ws_url(self) -> str:
        """Build WebSocket URL with token as query param."""
        url = self.base_url
        if self.api_key:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}token={self.api_key}"
        return url

    async def _ensure_connection(self):
        """Connect and perform MCP initialize handshake if not already done."""
        aio = await _get_aiohttp()
        if self._ws is not None and not self._ws.closed:
            return

        # Fast pre-check: if the forge daemon port is not reachable, fail
        # immediately instead of waiting for TCP/WebSocket timeout (~10 s).
        import socket as _socket
        from urllib.parse import urlparse as _urlparse
        _parsed = _urlparse(
            self.base_url.replace("ws://", "http://").replace("wss://", "https://")
        )
        _forge_host = _parsed.hostname or "127.0.0.1"
        _forge_port = _parsed.port or 42070
        try:
            _sock = _socket.create_connection((_forge_host, _forge_port), timeout=0.5)
            _sock.close()
        except OSError:
            raise ConnectionError(
                f"Forge daemon not reachable at {_forge_host}:{_forge_port} "
                "(start the NAVIG Forge extension in VS Code)"
            )

        session = await self._get_session()
        self._ws = await session.ws_connect(
            self._ws_url(),
            timeout=aio.ClientTimeout(total=10),
            heartbeat=30,
        )
        self._initialized = False

        # MCP initialize handshake
        if not self._initialized:
            self._request_id += 1
            init_req = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "navig-daemon", "version": "1.0.0"},
                },
            }
            await self._ws.send_json(init_req)
            resp = await self._ws.receive_json(timeout=10)
            if resp.get("error"):
                raise RuntimeError(f"MCP initialize failed: {resp['error']}")

            # Send initialized notification
            self._request_id += 1
            await self._ws.send_json({
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "initialized",
                "params": {},
            })
            # Read ack (initialized response)
            await self._ws.receive_json(timeout=5)
            self._initialized = True

    async def _call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Send MCP tools/call and return parsed result."""
        import json as _json

        await self._ensure_connection()
        self._request_id += 1
        req = {
            "jsonrpc": "2.0",
            "id": self._request_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
        await self._ws.send_json(req)
        resp = await self._ws.receive_json(timeout=120)

        if resp.get("error"):
            raise RuntimeError(f"MCP tool error: {resp['error'].get('message', resp['error'])}")

        result = resp.get("result", {})
        if result.get("isError"):
            content_text = ""
            for c in result.get("content", []):
                if c.get("type") == "text":
                    content_text += c.get("text", "")
            raise RuntimeError(f"MCP tool '{tool_name}' failed: {content_text}")

        # Parse text content
        for c in result.get("content", []):
            if c.get("type") == "text":
                try:
                    return _json.loads(c["text"])
                except (_json.JSONDecodeError, KeyError):
                    return {"text": c.get("text", "")}
        return {}

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        t0 = time.monotonic()

        tool_args: dict = {
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_tokens": max_tokens,
        }
        if model:
            tool_args["model"] = model
        # Forward purpose hint so navig-bridge picks the optimal Copilot model
        purpose = kwargs.get("purpose")
        if purpose:
            tool_args["purpose"] = purpose

        result = await self._call_tool("vscode_llm_chat", tool_args)

        latency = int((time.monotonic() - t0) * 1000)
        text = result.get("text", "")
        model_used = result.get("model_used", model or "copilot-mcp")
        model_name = result.get("model_name", "")

        return LLMResponse(
            content=text,
            model=model_used,
            provider=self.name,
            latency_ms=latency,
            finish_reason="stop",
            raw={"model_name": model_name, "via": "mcp_websocket"},
        )

    async def is_available(self) -> bool:
        """Probe via MCP ping."""
        try:
            await self._ensure_connection()
            self._request_id += 1
            await self._ws.send_json({
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": "ping",
                "params": {},
            })
            resp = await self._ws.receive_json(timeout=5)
            return not resp.get("error")
        except Exception:
            self._ws = None
            self._initialized = False
            return False

    async def close(self):
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._initialized = False
        await super().close()

    async def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ):
        """
        MCP tools/call is not streaming, so fall back to buffered chat
        and yield the full response as a single chunk.
        """
        resp = await self.chat(model, messages, temperature, max_tokens, **kwargs)
        yield resp.content


# ── GitHub Models (Azure AI Inference) ──────────────────────────────

class GitHubModelsProvider(LLMProvider):
    """
    GitHub Models — free-tier AI models via Azure AI Inference.

    Uses a GitHub Personal Access Token (PAT) for auth.  The endpoint is
    OpenAI-compatible so the wire format is identical to OpenAIProvider,
    but the base URL and default model differ.

    **Multi-model fallback**: When a model returns 429 (rate limit) or
    a server error (5xx), the provider automatically tries the next model
    in a configurable fallback chain.  This maximizes throughput across
    GitHub's free tier limits.

    Rate limit tiers (Copilot Free, per-model):
      High (10 rpm / 50 rpd): gpt-4o, Meta-Llama-3.1-405B, Mistral-large-2407
      Low  (15 rpm / 150 rpd): gpt-4o-mini, Meta-Llama-3.1-70B, Meta-Llama-3.1-8B,
                                 Mistral-Nemo, AI21-Jamba-1.5-Large

    Config in ``~/.navig/config.yaml``::

        github_models:
          token: ghp_xxxx   # GitHub PAT with "models" scope
          model: gpt-4o     # primary model

    Or via environment variable::

        GITHUB_TOKEN=ghp_xxxx
    """

    name = "github_models"

    BASE_URL = "https://models.inference.ai.azure.com"
    DEFAULT_MODEL = "gpt-4o"

    # ── Fallback chains per "quality tier" ──
    # high_quality: best reasoning models (High rate-limit tier = 50 rpd each)
    # fast_cheap: fast/cheap models (Low rate-limit tier = 150 rpd each)
    # These are tried in order when the requested model hits a rate limit.
    FALLBACK_CHAINS = {
        # For "big" quality requests (reasoning, planning, complex)
        "high_quality": [
            "gpt-4o",
            "Meta-Llama-3.1-405B-Instruct",
            "Mistral-large-2407",
        ],
        # For "small" quality requests (chat, greetings, simple Q&A)
        "fast_cheap": [
            "gpt-4o-mini",
            "Meta-Llama-3.1-70B-Instruct",
            "Mistral-Nemo",
            "AI21-Jamba-1.5-Large",
            "Meta-Llama-3.1-8B-Instruct",
        ],
    }

    # Map from any requested model → which fallback chain to use
    MODEL_TO_CHAIN = {
        "gpt-4o": "high_quality",
        "Meta-Llama-3.1-405B-Instruct": "high_quality",
        "Mistral-large-2407": "high_quality",
        "gpt-4o-mini": "fast_cheap",
        "Meta-Llama-3.1-70B-Instruct": "fast_cheap",
        "Mistral-Nemo": "fast_cheap",
        "AI21-Jamba-1.5-Large": "fast_cheap",
        "Meta-Llama-3.1-8B-Instruct": "fast_cheap",
    }

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        # Resolve token: explicit param → env var → vault → config
        resolved_key = api_key or os.getenv("GITHUB_TOKEN", "")
        if not resolved_key:
            resolved_key = self._resolve_token()
        super().__init__(
            base_url=base_url or self.BASE_URL,
            api_key=resolved_key,
            **kwargs,
        )
        # Track rate limit state per model: model → timestamp when limit was hit
        self._rate_limited: Dict[str, float] = {}
        # Track consecutive failures per model
        self._fail_counts: Dict[str, int] = defaultdict(int)

    @staticmethod
    def _resolve_token() -> str:
        """Resolve GitHub Models token from vault or config."""
        # Vault
        try:
            from navig.vault import get_vault
            vault = get_vault()
            secret = vault.get_secret("github_models", "token", caller="github_models_provider")
            if secret:
                val = secret.reveal().strip() if hasattr(secret, "reveal") else str(secret).strip()
                if val:
                    return val
        except Exception:
            pass
        # Config file
        try:
            from navig.config import get_config_manager
            cfg = get_config_manager().global_config or {}
            token = cfg.get("github_models", {}).get("token", "")
            if token:
                return token
        except Exception:
            pass
        return ""

    def _get_fallback_chain(self, model: str) -> List[str]:
        """Get the ordered fallback chain for a model, starting with the model itself."""
        chain_name = self.MODEL_TO_CHAIN.get(model, "high_quality")
        chain = list(self.FALLBACK_CHAINS.get(chain_name, []))

        # Ensure requested model is first
        if model in chain:
            chain.remove(model)
        chain.insert(0, model)

        # Filter out models that were rate-limited less than 60s ago
        now = time.monotonic()
        available = []
        rate_limited = []
        for m in chain:
            limit_time = self._rate_limited.get(m, 0)
            if now - limit_time > 60:  # 60s cooldown
                available.append(m)
            else:
                rate_limited.append(m)

        # Put rate-limited models at the end (they might have recovered)
        return available + rate_limited

    async def chat(self, model, messages, temperature=0.7, max_tokens=4096, **kw):
        if not self.api_key:
            raise RuntimeError(
                "GitHub token not set. "
                "Set GITHUB_TOKEN env var or github_models.token in ~/.navig/config.yaml"
            )

        chain = self._get_fallback_chain(model or self.DEFAULT_MODEL)
        last_error = None

        for attempt_model in chain:
            try:
                result = await self._chat_single(
                    attempt_model, messages, temperature, max_tokens, **kw
                )
                # Success — clear any failure state
                self._fail_counts[attempt_model] = 0
                if attempt_model != (model or self.DEFAULT_MODEL):
                    logger.info(
                        "GitHub Models fallback: %s → %s (success)",
                        model, attempt_model,
                    )
                return result

            except _RateLimitError as e:
                self._rate_limited[attempt_model] = time.monotonic()
                self._fail_counts[attempt_model] += 1
                logger.warning(
                    "GitHub Models rate limited on %s, trying next in chain...",
                    attempt_model,
                )
                last_error = e
                continue

            except _ServerError as e:
                self._fail_counts[attempt_model] += 1
                logger.warning(
                    "GitHub Models server error on %s: %s, trying next...",
                    attempt_model, e,
                )
                last_error = e
                continue

        # All models in chain exhausted
        raise RuntimeError(
            f"All GitHub Models exhausted for chain starting at {model}. "
            f"Last error: {last_error}"
        )

    async def _chat_single(
        self, model: str, messages, temperature=0.7, max_tokens=4096, **kw
    ):
        """Make a single chat request to one specific model."""
        session = await self._get_session()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        t0 = time.monotonic()
        aio = await _get_aiohttp()
        async with session.post(
            url, headers=headers, json=payload,
            timeout=aio.ClientTimeout(total=60),
        ) as resp:
            if resp.status == 401:
                raise RuntimeError(
                    "GitHub Models returned 401 — check your GITHUB_TOKEN or "
                    "github_models.token in ~/.navig/config.yaml"
                )
            if resp.status == 429:
                text = await resp.text()
                raise _RateLimitError(
                    f"Rate limited on {model}: {text}"
                )
            if resp.status >= 500:
                text = await resp.text()
                raise _ServerError(
                    f"Server error {resp.status} on {model}: {text}"
                )
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"GitHub Models error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model),
            provider=self.name,
            latency_ms=latency,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", ""),
            raw=data,
        )

    async def is_available(self) -> bool:
        """Check if a GitHub token is configured."""
        if not self.api_key:
            # Also check config
            try:
                from navig.config import get_config_manager
                cfg = get_config_manager().global_config or {}
                token = cfg.get("github_models", {}).get("token", "")
                if token:
                    self.api_key = token
                    return True
            except Exception:
                pass
            return False
        return True


class _RateLimitError(Exception):
    """Raised when GitHub Models returns 429."""
    pass


class _ServerError(Exception):
    """Raised when GitHub Models returns 5xx."""
    pass


# ── Factory ─────────────────────────────────────────────────────────

_PROVIDER_MAP = {
    "github_models": GitHubModelsProvider,
    "github": GitHubModelsProvider,
    "ollama": OllamaProvider,
    "openrouter": OpenRouterProvider,
    "openai": OpenAIProvider,
    "llamacpp": LlamaCppProvider,
    "llama.cpp": LlamaCppProvider,
    "llama_cpp": LlamaCppProvider,
}


def create_provider(name: str, **kwargs) -> LLMProvider:
    """
    Instantiate a provider by name.

    >>> p = create_provider("ollama", base_url="http://myhost:11434")
    >>> p = create_provider("openrouter", api_key="sk-or-...")
    """
    cls = _PROVIDER_MAP.get(name.lower())
    if cls is None:
        raise ValueError(
            f"Unknown provider {name!r}. "
            f"Available: {', '.join(sorted(_PROVIDER_MAP))}"
        )
    return cls(**kwargs)


def list_provider_names() -> List[str]:
    """Return canonical provider names."""
    return sorted({cls.name for cls in _PROVIDER_MAP.values()})


# ── Helpers ─────────────────────────────────────────────────────────

def _timeout(seconds: float):
    """Create an aiohttp ClientTimeout."""
    import aiohttp
    return aiohttp.ClientTimeout(total=seconds)
