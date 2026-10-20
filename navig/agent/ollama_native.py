"""navig.agent.ollama_native — Ollama's NATIVE chat API, for jobs that pin one local model.

Why a second Ollama client exists next to ``llm_providers.OllamaProvider``:

* ``OllamaProvider`` speaks the OpenAI-compatible ``/v1/chat/completions`` endpoint. Ollama
  **ignores** ``num_ctx`` there, so the model runs at its default context window (2-4k tokens
  depending on version) and a long prompt is truncated from the FRONT with no error — the
  model then "fixes" a file it only half saw.
* It also caps every call at a hard 45 s total. A 32B model running on CPU needs minutes for
  one answer, so that path could never succeed for it.

The native ``/api/chat`` endpoint takes ``options.num_ctx`` and reports its own timings, which
is what an unattended fixer (``navig evolve sweep``) and its benchmark need. This module is
deliberately small and synchronous: one request, one answer, typed errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from navig.providers._local_defaults import ollama_base_url

#: Context window asked of the model. Large enough for a ~120-line code window plus a
#: traceback, small enough that a 7B model's KV cache still fits beside the desktop on 8 GB.
DEFAULT_NUM_CTX = 8192


class OllamaError(RuntimeError):
    """Ollama answered with an error, timed out, or could not be reached."""


@dataclass(frozen=True)
class OllamaReply:
    """One completed chat turn plus the timings Ollama measured for it."""

    content: str
    model: str
    prompt_tokens: int = 0
    output_tokens: int = 0
    load_s: float = 0.0
    prompt_s: float = 0.0
    output_s: float = 0.0
    total_s: float = 0.0

    @property
    def output_tok_per_s(self) -> float:
        return self.output_tokens / self.output_s if self.output_s > 0 else 0.0


def _ns_to_s(value: Any) -> float:
    try:
        return float(value or 0) / 1e9
    except (TypeError, ValueError):
        return 0.0


def list_models(base_url: str | None = None, timeout: float = 5.0) -> dict[str, int]:
    """Installed models → size in bytes. Raises :class:`OllamaError` when unreachable."""
    import httpx  # noqa: PLC0415 — lazy: keep `navig help` fast

    url = f"{(base_url or ollama_base_url()).rstrip('/')}/api/tags"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — every failure mode means "not reachable"
        raise OllamaError(f"Ollama is not reachable at {url}: {exc}") from exc
    return {
        str(m.get("name")): int(m.get("size") or 0)
        for m in data.get("models") or []
        if m.get("name")
    }


def chat(
    model: str,
    messages: list[dict[str, str]],
    *,
    num_ctx: int = DEFAULT_NUM_CTX,
    temperature: float = 0.0,
    timeout: float = 300.0,
    keep_alive: str = "30m",
    base_url: str | None = None,
) -> OllamaReply:
    """Send one chat request to *model* and return the full reply.

    ``timeout`` bounds the whole request (connect + generation). Raises
    :class:`OllamaError` for an unreachable server, a non-200 answer, a timeout, or an empty
    reply — an empty completion is never returned as success.
    """
    import httpx  # noqa: PLC0415

    url = f"{(base_url or ollama_base_url()).rstrip('/')}/api/chat"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "keep_alive": keep_alive,
        "options": {"num_ctx": int(num_ctx), "temperature": float(temperature)},
    }
    try:
        resp = httpx.post(url, json=payload, timeout=httpx.Timeout(timeout, connect=10.0))
    except httpx.TimeoutException as exc:
        raise OllamaError(f"{model} did not answer within {timeout:.0f}s") from exc
    except httpx.HTTPError as exc:
        raise OllamaError(f"Ollama is not reachable at {url}: {exc}") from exc
    if resp.status_code != 200:
        raise OllamaError(f"Ollama error {resp.status_code} for {model}: {resp.text[:300]}")
    data = resp.json()
    content = str((data.get("message") or {}).get("content") or "")
    if not content.strip():
        raise OllamaError(f"{model} returned an empty reply")
    return OllamaReply(
        content=content,
        model=str(data.get("model") or model),
        prompt_tokens=int(data.get("prompt_eval_count") or 0),
        output_tokens=int(data.get("eval_count") or 0),
        load_s=_ns_to_s(data.get("load_duration")),
        prompt_s=_ns_to_s(data.get("prompt_eval_duration")),
        output_s=_ns_to_s(data.get("eval_duration")),
        total_s=_ns_to_s(data.get("total_duration")),
    )
