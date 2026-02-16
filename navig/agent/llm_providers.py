"""
NAVIG Unified LLM Provider Layer

Pluggable provider interface that abstracts Ollama, OpenRouter, OpenAI,
and llama.cpp behind a single async ``chat()`` method.

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


# ── Forge (VS Code Copilot Bridge) ─────────────────────────────────

class ForgeProvider(LLMProvider):
    """
    Bridge to a VS Code Copilot LLM server exposed by navig-bridge.

    The Forge extension runs an HTTP server (default :43821) that proxies
    requests through ``vscode.lm`` to GitHub Copilot models.  On Ubuntu
    the daemon reaches it via an SSH reverse-tunnel.

    Config in ``~/.navig/config.yaml``::

        forge:
          url: http://127.0.0.1:43821
          token: <shared-secret>

    The server speaks its own ChatRequest/ChatResponse protocol, so this
    provider translates between the standard ``messages`` list and the
    Forge wire format.
    """

    name = "forge"

    # Default port mirrors navig-bridge chatVscodeLlmPort setting
    DEFAULT_URL = "http://127.0.0.1:43821"

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        super().__init__(
            base_url=base_url or self.DEFAULT_URL,
            api_key=api_key,
            **kwargs,
        )

    # ── helpers ──

    def _auth_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _messages_to_forge(messages: List[Dict[str, str]]) -> dict:
        """
        Convert a standard ``[{role, content}, ...]`` messages list into
        the Forge ``ChatRequest`` shape: ``{text, conversation, scope}``.

        The *last user message* becomes ``text``; the full history
        (including that message) is sent as ``conversation``.
        """
        conversation = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
        ]

        # Extract the last user message as the primary text
        text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                text = m["content"]
                break
        if not text and messages:
            text = messages[-1]["content"]

        return {
            "text": text,
            "conversation": conversation,
            "scope": "personal",
        }

    # ── core API ──

    async def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs,
    ) -> LLMResponse:
        session = await self._get_session()
        url = f"{self.base_url}/vscode-llm/chat"
        payload = self._messages_to_forge(messages)
        if model:
            payload["model"] = model

        t0 = time.monotonic()
        aio = await _get_aiohttp()
        async with session.post(
            url,
            json=payload,
            headers=self._auth_headers(),
            timeout=aio.ClientTimeout(total=120),
        ) as resp:
            if resp.status == 401:
                raise RuntimeError(
                    "Forge LLM server returned 401 Unauthorized — "
                    "check forge.token in ~/.navig/config.yaml"
                )
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Forge LLM error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        # Forge ChatResponse: {text, metadata?: {model, provider, latencyMs, tokenUsage?}}
        meta = data.get("metadata") or {}
        token_usage = meta.get("tokenUsage") or {}

        return LLMResponse(
            content=data.get("text", ""),
            model=meta.get("model", model or "copilot"),
            provider=self.name,
            latency_ms=latency,
            prompt_tokens=token_usage.get("prompt", 0),
            completion_tokens=token_usage.get("completion", 0),
            finish_reason="stop",
            raw=data,
        )

    async def is_available(self) -> bool:
        """Probe the /vscode-llm/health endpoint."""
        try:
            session = await self._get_session()
            async with session.get(
                f"{self.base_url}/vscode-llm/health",
                timeout=_timeout(3),
            ) as r:
                if r.status != 200:
                    return False
                body = await r.json()
                return body.get("status") == "ready"
        except Exception:
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
        Stream text chunks from the Forge LLM bridge via SSE.

        Uses ``POST /vscode-llm/chat/stream`` which returns Server-Sent
        Events.  Each event carries ``{text, done, metadata?}``.
        """
        import json as _json

        session = await self._get_session()
        url = f"{self.base_url}/vscode-llm/chat/stream"
        payload = self._messages_to_forge(messages)
        if model:
            payload["model"] = model

        aio = await _get_aiohttp()
        async with session.post(
            url,
            json=payload,
            headers=self._auth_headers(),
            timeout=aio.ClientTimeout(total=120),
        ) as resp:
            if resp.status == 401:
                raise RuntimeError(
                    "Forge LLM server returned 401 Unauthorized — "
                    "check forge.token in ~/.navig/config.yaml"
                )
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"Forge LLM stream error ({resp.status}): {text}")

            # Parse SSE: lines starting with "data: "
            async for line in resp.content:
                decoded = line.decode("utf-8", errors="replace").strip()
                if not decoded.startswith("data: "):
                    continue
                raw = decoded[6:]  # strip "data: " prefix
                try:
                    event = _json.loads(raw)
                except _json.JSONDecodeError:
                    continue

                if event.get("error"):
                    raise RuntimeError(f"Forge stream error: {event['error']}")

                text_chunk = event.get("text", "")
                if text_chunk:
                    yield text_chunk

                if event.get("done"):
                    break


# ── GitHub Models (Azure AI Inference) ──────────────────────────────

class GitHubModelsProvider(LLMProvider):
    """
    GitHub Models — free-tier AI models via Azure AI Inference.

    Uses a GitHub Personal Access Token (PAT) for auth.  The endpoint is
    OpenAI-compatible so the wire format is identical to OpenAIProvider,
    but the base URL and default model differ.

    Config in ``~/.navig/config.yaml``::

        github_models:
          token: ghp_xxxx   # GitHub PAT with "models" scope
          model: gpt-4o     # or any model from github.com/marketplace/models

    Or via environment variable::

        GITHUB_TOKEN=ghp_xxxx
    """

    name = "github_models"

    BASE_URL = "https://models.inference.ai.azure.com"
    DEFAULT_MODEL = "gpt-4o"

    def __init__(self, base_url: str = "", api_key: str = "", **kwargs):
        super().__init__(
            base_url=base_url or self.BASE_URL,
            api_key=api_key or os.getenv("GITHUB_TOKEN", ""),
            **kwargs,
        )

    async def chat(self, model, messages, temperature=0.7, max_tokens=4096, **kw):
        if not self.api_key:
            raise RuntimeError(
                "GitHub token not set. "
                "Set GITHUB_TOKEN env var or github_models.token in ~/.navig/config.yaml"
            )

        session = await self._get_session()
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model or self.DEFAULT_MODEL,
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
            if resp.status != 200:
                text = await resp.text()
                raise RuntimeError(f"GitHub Models error ({resp.status}): {text}")
            data = await resp.json()
        latency = int((time.monotonic() - t0) * 1000)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        return LLMResponse(
            content=choice["message"]["content"],
            model=data.get("model", model or self.DEFAULT_MODEL),
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


# ── Factory ─────────────────────────────────────────────────────────

_PROVIDER_MAP = {
    "forge": ForgeProvider,
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
