"""Pre-flight LLM availability probe.

Tier hierarchy (cheapest → most capable):
  T0  — Ollama         local, strictly free, no key
  T1  — Free cloud     GitHub Models / Groq / SiliconFlow / Together free-tier
  T2  — Bridge         VS Code Copilot via mcp_bridge tunnel (no key)
  T3  — Paid cloud     OpenAI / Anthropic / DeepSeek / xAI / Mistral / …

Usage::

    from navig.agent.llm_probe import probe_llm, TIER_GUIDE

    ok, tier_label, model_label, note = await probe_llm()
    if not ok:
        print(TIER_GUIDE)
"""
from __future__ import annotations

import asyncio
import os
from typing import NamedTuple

from loguru import logger

from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class ProbeResult(NamedTuple):
    reachable: bool
    tier: str           # e.g. "T0 • Ollama"
    model: str          # e.g. "llama3:8b"
    note: str           # short human-readable note


# ---------------------------------------------------------------------------
# Tier guide (shown when no backend reachable)
# ---------------------------------------------------------------------------

TIER_GUIDE = """\
🤖 **No AI backend reachable — choose your tier:**

**T0 — Free, no account (local)**
• Install Ollama: https://ollama.com
• `ollama pull llama3.2`  →  starts at http://localhost:11434

**T1 — Free cloud (API key required)**
• GitHub Models  — free with any GitHub account (needs `GITHUB_TOKEN`)
  https://github.com/marketplace/models
• Groq  — free tier, fast inference (`GROQ_API_KEY`)
  https://console.groq.com
• SiliconFlow  — free credits (`SILICONFLOW_API_KEY`)
  https://siliconflow.cn
• Together AI  — free credits (`TOGETHER_API_KEY`)
  https://api.together.xyz

**T2 — Bridge (VS Code Copilot)**
• Open VS Code → install NAVIG Bridge extension
• Your Copilot subscription routes here automatically

**T3 — Paid cloud**
• OpenAI (`OPENAI_API_KEY`)
• Anthropic Claude (`ANTHROPIC_API_KEY`)
• DeepSeek (`DEEPSEEK_API_KEY`)
• xAI Grok (`XAI_API_KEY`)

Set keys in `~/.navig/config.yaml` or as environment variables.
"""


# ---------------------------------------------------------------------------
# T0 — Ollama
# ---------------------------------------------------------------------------

_OLLAMA_BASE = "http://127.0.0.1:11434"


async def _probe_ollama() -> ProbeResult | None:
    """Check if Ollama daemon is running and return the best available model."""
    import httpx  # lazy import

    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{_OLLAMA_BASE}/api/tags")
            if resp.status_code != 200:
                return None

            data = resp.json()
            models = [m.get("name", "") for m in data.get("models", [])]
            if not models:
                return ProbeResult(
                    reachable=True,
                    tier="T0",
                    model="(no models pulled yet)",
                    note="Ollama running but no models. Run: `ollama pull llama3.2`",
                )

            # Prefer a ~8b chat model; fall back to first available
            preferred = _pick_chat_model(models)
            return ProbeResult(
                reachable=True,
                tier="T0",
                model=preferred,
                note=f"Ollama local — {len(models)} model(s) available",
            )
    except (httpx.ConnectError, httpx.TimeoutException):
        return None
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_probe: Ollama check error: {}", exc)
        return None


def _pick_chat_model(models: list[str]) -> str:
    """Pick the most suitable chat model from an Ollama model list."""
    priority_patterns = [
        "llama3.2:3b", "llama3.2", "llama3:8b", "llama3",
        "mistral:7b", "mistral", "phi3:mini", "phi3",
        "gemma2:2b", "gemma2", "qwen2.5:7b", "qwen2.5",
    ]
    for pat in priority_patterns:
        for m in models:
            if pat in m:
                return m
    return models[0]


# ---------------------------------------------------------------------------
# T1 — Free cloud (key check only — no live API ping to save latency)
# ---------------------------------------------------------------------------

_FREE_CLOUD_PROVIDERS: list[tuple[str, str, str, str]] = [
    # (env_var, tier_label, model_hint, human_name)
    ("GROQ_API_KEY",       "T1", "llama-3.3-70b-versatile", "Groq (free tier)"),
    ("GITHUB_TOKEN",       "T1", "gpt-4o-mini",              "GitHub Models (free)"),
    ("SILICONFLOW_API_KEY","T1", "deepseek-ai/DeepSeek-V3",  "SiliconFlow (free credits)"),
    ("TOGETHER_API_KEY",   "T1", "meta-llama/Meta-Llama-3-8B-Instruct-Turbo", "Together AI (free)"),
]


def _probe_free_cloud() -> ProbeResult | None:
    """Key-presence check for T1 free cloud providers (no live ping)."""
    for env_var, tier, model_hint, name in _FREE_CLOUD_PROVIDERS:
        key = os.environ.get(env_var) or _read_navig_config_key(env_var)
        if key and len(key) > 4:
            return ProbeResult(
                reachable=True,
                tier=tier,
                model=model_hint,
                note=f"{name} — key found in env ({env_var})",
            )
    return None


# ---------------------------------------------------------------------------
# T2 — Bridge (mcp_bridge / VS Code Copilot tunnel)
# ---------------------------------------------------------------------------

_BRIDGE_PORT = BRIDGE_DEFAULT_PORT  # navig-bridge default MCP WebSocket port


async def _probe_bridge() -> ProbeResult | None:
    """Attempt TCP connect to mcp_bridge Bridge port."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=0.8) as client:
            resp = await client.get(f"http://127.0.0.1:{_BRIDGE_PORT}/health")
            if resp.status_code < 400:
                return ProbeResult(
                    reachable=True,
                    tier="T2",
                    model="bridge-copilot",
                    note=f"Bridge (VS Code Copilot) active on port {BRIDGE_DEFAULT_PORT}",
                )
    except (httpx.ConnectError, httpx.TimeoutException):
        pass  # service unreachable; skip probe
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_probe: Bridge check error: {}", exc)

    return None


# ---------------------------------------------------------------------------
# T3 — Paid cloud (key check only)
# ---------------------------------------------------------------------------

_PAID_PROVIDERS: list[tuple[str, str, str, str]] = [
    ("OPENAI_API_KEY",    "T3", "gpt-4o-mini",       "OpenAI"),
    ("ANTHROPIC_API_KEY", "T3", "claude-3-haiku",     "Anthropic"),
    ("DEEPSEEK_API_KEY",  "T3", "deepseek-chat",      "DeepSeek"),
    ("XAI_API_KEY",       "T3", "grok-3-mini-fast",   "xAI Grok"),
    ("MISTRAL_API_KEY",   "T3", "mistral-small-latest","Mistral"),
]


def _probe_paid() -> ProbeResult | None:
    """Key-presence check for T3 paid providers."""
    for env_var, tier, model_hint, name in _PAID_PROVIDERS:
        key = os.environ.get(env_var) or _read_navig_config_key(env_var)
        if key and len(key) > 8:
            return ProbeResult(
                reachable=True,
                tier=tier,
                model=model_hint,
                note=f"{name} ({env_var} configured)",
            )
    return None


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

def _read_navig_config_key(env_var: str) -> str:
    """Read an API key from navig config if not in environment."""
    try:
        from navig.config import get as cfg_get
        return cfg_get(f"providers.{env_var.lower()}", "") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def probe_llm(prefer_local: bool = True) -> ProbeResult:
    """Run all tier probes and return the highest-priority available backend.

    Args:
        prefer_local: If True, T0 (Ollama) wins over T1 even if T1 key present.

    Returns:
        ProbeResult with reachable=True if any backend found,
        or ProbeResult(reachable=False, …) with TIER_GUIDE in note.
    """
    # Run async checks concurrently
    ollama_task = asyncio.create_task(_probe_ollama())
    bridge_task = asyncio.create_task(_probe_bridge())

    # Synchronous key checks (instant)
    t1_result = _probe_free_cloud()
    t3_result = _probe_paid()

    ollama_result = await ollama_task
    bridge_result = await bridge_task

    # Priority order: T0 → T2 → T1 → T3
    # (local-first by default; Bridge before cloud as no latency cost)
    ordered: list[ProbeResult | None]
    if prefer_local:
        ordered = [ollama_result, bridge_result, t1_result, t3_result]
    else:
        ordered = [bridge_result, t1_result, ollama_result, t3_result]

    for result in ordered:
        if result is not None and result.reachable:
            logger.debug(
                "llm_probe: {} reachable — {} ({})",
                result.tier, result.model, result.note,
            )
            return result

    logger.info("llm_probe: no AI backend reachable — showing tier guide")
    return ProbeResult(
        reachable=False,
        tier="none",
        model="",
        note=TIER_GUIDE,
    )


def probe_llm_sync(prefer_local: bool = True) -> ProbeResult:
    """Synchronous wrapper around :func:`probe_llm` for non-async callers."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in an event loop — run in a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                future = ex.submit(asyncio.run, probe_llm(prefer_local))
                return future.result(timeout=5)
        return loop.run_until_complete(probe_llm(prefer_local))
    except Exception as exc:  # noqa: BLE001
        logger.debug("llm_probe: sync probe failed: {}", exc)
        return ProbeResult(reachable=False, tier="none", model="", note=TIER_GUIDE)
