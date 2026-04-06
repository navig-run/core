"""
NAVIG Provider Registry — Single Source of Truth

Every AI provider known to NAVIG is declared here as a ``ProviderManifest``.
All other surfaces (Wizard, Telegram bot, NavigCore verifier, fallback manager)
must read from this registry instead of maintaining their own lists.

Adding a new provider:
  1. Add a ``ProviderManifest`` entry to ``ALL_PROVIDERS`` below.
  2. Add a factory entry in ``navig.agent.llm_providers._PROVIDER_MAP``.
  3. Run ``verify_all_providers()`` and confirm zero failures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

ProviderTier = Literal["cloud", "local", "proxy"]


@dataclass
class ProviderManifest:
    """
    Metadata describing a single AI provider.

    Fields
    ------
    id              Canonical snake_case key used everywhere (registry, vault, CLI).
    display_name    Human-readable label for UI surfaces.
    description     One-line description shown in /providers and navig init.
    tier            "cloud" (requires remote API), "local" (no network, runs on device),
                    "proxy" (local proxy that handles upstream auth itself).
    env_vars        Environment variable names that can supply the API key.
    vault_keys      Vault paths where the key may be stored (checked in order).
    requires_key    False for local/proxy providers that need no user API key.
    local_probe     host:port to TCP-probe for local providers (e.g. "127.0.0.1:11434").
    models          Representative model IDs (not exhaustive; informational only).
    emoji           Emoji for Telegram keyboard buttons.
    enabled         Disabled providers are hidden from all surfaces unless explicitly
                    requested.  New providers from lab ship as enabled=False.
    auth_mode       "api_key" | "oauth" | "token" | "none"
    """

    id: str
    display_name: str
    description: str
    tier: ProviderTier
    env_vars: list[str] = field(default_factory=list)
    vault_keys: list[str] = field(default_factory=list)
    requires_key: bool = True
    local_probe: str | None = None
    models: list[str] = field(default_factory=list)
    emoji: str = "🤖"
    enabled: bool = True
    auth_mode: Literal["api_key", "oauth", "token", "none"] = "api_key"


# ─────────────────────────────────────────────────────────────────────────────
# ALL_PROVIDERS — the canonical list
# Every provider in BUILTIN_PROVIDERS, _PROVIDER_MAP, and the Telegram UI
# must have a corresponding entry here.
# ─────────────────────────────────────────────────────────────────────────────

ALL_PROVIDERS: list[ProviderManifest] = [
    # ── Cloud: OpenAI ─────────────────────────────────────────────────────────
    ProviderManifest(
        id="openai",
        display_name="OpenAI",
        description="GPT-4.1, GPT-4o, o-series reasoning models — latest OpenAI lineup.",
        tier="cloud",
        env_vars=["OPENAI_API_KEY"],
        vault_keys=["openai/api-key", "openai/api_key"],
        models=[
            # GPT-4.1 series (April 2025)
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4.1-nano",
            # GPT-4o series
            "gpt-4o",
            "gpt-4o-mini",
            # Reasoning / o-series
            "o4-mini",
            "o3",
            "o3-mini",
            "o1",
            "o1-mini",
        ],
        emoji="🤖",
    ),
    # ── Cloud: Anthropic ──────────────────────────────────────────────────────
    ProviderManifest(
        id="anthropic",
        display_name="Anthropic",
        description="Claude 3.7 Sonnet, Claude 3.5 series — extended thinking and long context.",
        tier="cloud",
        env_vars=["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
        vault_keys=["anthropic/api-key", "anthropic/api_key"],
        models=[
            # Claude 3.7 (February 2025 — extended thinking)
            "claude-3-7-sonnet-20250219",
            # Claude 3.5 series
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            # Claude 3 (legacy)
            "claude-3-opus-20240229",
        ],
        emoji="🟣",
    ),
    # ── Cloud: Google / Gemini ────────────────────────────────────────────────
    ProviderManifest(
        id="google",
        display_name="Google Gemini",
        description="Gemini 2.5 Pro/Flash, 2.0 Flash, 1.5 Pro/Flash — multimodal up to 2M context.",
        tier="cloud",
        env_vars=["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        vault_keys=["google/api-key", "google/api_key", "gemini/api-key"],
        models=[
            "gemini-2.5-pro-preview-05-06",
            "gemini-2.5-flash-preview-04-17",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
        emoji="🔵",
    ),
    # ── Cloud: OpenRouter ─────────────────────────────────────────────────────
    ProviderManifest(
        id="openrouter",
        display_name="OpenRouter",
        description="Unified gateway to 100+ models — Claude, GPT, Gemini, Llama, DeepSeek.",
        tier="cloud",
        env_vars=["OPENROUTER_API_KEY"],
        vault_keys=["openrouter/api-key", "openrouter/api_key"],
        models=[
            # Anthropic
            "anthropic/claude-3-7-sonnet",
            "anthropic/claude-3-5-sonnet",
            "anthropic/claude-3-5-haiku",
            # OpenAI
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/o3-mini",
            # Google
            "google/gemini-2.5-pro-preview-05-06",
            "google/gemini-2.5-flash-preview-04-17",
            "google/gemini-2.0-flash-001",
            # Meta Llama
            "meta-llama/llama-3.3-70b-instruct",
            "meta-llama/llama-3.1-8b-instruct",
            # DeepSeek
            "deepseek/deepseek-chat-v3-0324",
            "deepseek/deepseek-r1",
            # xAI
            "x-ai/grok-3-beta",
            "x-ai/grok-3-mini-beta",
            # Mistral
            "mistralai/mistral-large-2411",
            "mistralai/mistral-small-3.1-24b-instruct",
            # Qwen
            "qwen/qwq-32b",
            "qwen/qwen-2.5-72b-instruct",
            # Misc
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "microsoft/phi-4",
        ],
        emoji="🌐",
    ),
    # ── Cloud: Groq ───────────────────────────────────────────────────────────
    ProviderManifest(
        id="groq",
        display_name="Groq",
        description="Ultra-fast LPU inference — Llama 3.3, DeepSeek-R1, Qwen, Gemma.",
        tier="cloud",
        env_vars=["GROQ_API_KEY"],
        vault_keys=["groq/api-key", "groq/api_key"],
        models=[
            "llama-3.3-70b-versatile",
            "llama-3.3-70b-specdec",
            "llama-3.1-8b-instant",
            "llama3-70b-8192",
            "mixtral-8x7b-32768",
            "deepseek-r1-distill-llama-70b",
            "deepseek-r1-distill-qwen-32b",
            "qwen-qwq-32b",
            "qwen2.5-72b-instruct",
            "gemma2-9b-it",
            "compound-beta",
        ],
        emoji="⚡",
    ),
    # ── Cloud: NVIDIA NIM ─────────────────────────────────────────────────────
    ProviderManifest(
        id="nvidia",
        display_name="NVIDIA NIM",
        description="NVIDIA-hosted inference — 40 RPM free tier, Llama, Mistral and more.",
        tier="cloud",
        env_vars=["NVIDIA_API_KEY", "NIM_API_KEY"],
        vault_keys=["nvidia/api-key", "nvidia/api_key"],
        models=[
            # Meta Llama
            "meta/llama-3.3-70b-instruct",
            "meta/llama-3.1-405b-instruct",
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            # NVIDIA Nemotron
            "nvidia/llama-3.1-nemotron-70b-instruct",
            "nvidia/llama-3.3-nemotron-super-49b-v1",
            # Mistral
            "mistralai/mistral-large-2-instruct",
            "mistralai/mistral-7b-instruct-v0.3",
            "mistralai/mixtral-8x22b-instruct-v0.1",
            # Google
            "google/gemma-3-27b-it",
            # Microsoft
            "microsoft/phi-4",
            "microsoft/phi-4-mini-instruct",
            "microsoft/phi-3-medium-4k-instruct",
            # DeepSeek
            "deepseek-ai/deepseek-r1",
            "deepseek-ai/deepseek-r1-distill-llama-70b",
            # Qwen
            "qwen/qwq-32b",
            "qwen/qwen2.5-72b-instruct",
        ],
        emoji="🟩",
    ),
    # ── Cloud: xAI / Grok ─────────────────────────────────────────────────────
    ProviderManifest(
        id="xai",
        display_name="xAI / Grok",
        description="Grok-3, Grok-2 and Grok Vision from xAI — real-time web access.",
        tier="cloud",
        env_vars=["XAI_API_KEY", "GROK_KEY"],
        vault_keys=["xai/api-key", "xai/api_key"],
        models=[
            "grok-3",
            "grok-3-fast",
            "grok-3-mini",
            "grok-3-mini-fast",
            "grok-2-1212",
            "grok-2-vision-1212",
        ],
        emoji="🌩",
    ),
    # ── Cloud: GitHub Models ──────────────────────────────────────────────────
    ProviderManifest(
        id="github_models",
        display_name="GitHub Models",
        description="Azure-hosted models via GitHub token — free tier for GitHub users.",
        tier="cloud",
        env_vars=["GITHUB_TOKEN", "GH_TOKEN"],
        vault_keys=["github/token", "github/api-key"],
        auth_mode="token",
        models=[
            # OpenAI
            "gpt-4.1",
            "gpt-4.1-mini",
            "gpt-4o",
            "gpt-4o-mini",
            "o4-mini",
            "o3-mini",
            # Microsoft Phi
            "phi-4",
            "phi-4-mini",
            "phi-3.5-mini-instruct",
            # Meta Llama
            "meta-llama-3.1-405b-instruct",
            "meta-llama-3.1-70b-instruct",
            # Mistral
            "mistral-large-2411",
            "mistral-small-2503",
            # DeepSeek
            "deepseek-r1",
            "deepseek-v3-0324",
        ],
        emoji="🐙",
    ),
    # ── Cloud: Mistral ────────────────────────────────────────────────────────
    ProviderManifest(
        id="mistral",
        display_name="Mistral AI",
        description="Mistral Large, Codestral and Mixtral series.",
        tier="cloud",
        env_vars=["MISTRAL_API_KEY"],
        vault_keys=["mistral/api-key", "mistral/api_key"],
        models=[
            "mistral-large-latest",
            "mistral-medium-latest",
            "mistral-small-latest",
            "codestral-latest",
            "open-mistral-nemo",
            "pixtral-large-latest",
        ],
        emoji="🌬",
        enabled=False,  # Key in PROVIDER_ENV_VARS but no ProviderConfig yet — opt-in
    ),
    # ── Cloud: Cerebras ───────────────────────────────────────────────────────
    ProviderManifest(
        id="cerebras",
        display_name="Cerebras",
        description="Wafer-scale chip inference — Llama at extreme speeds.",
        tier="cloud",
        env_vars=["CEREBRAS_API_KEY"],
        vault_keys=["cerebras/api-key"],
        models=[
            "llama-3.3-70b",
            "llama3.1-70b",
            "llama3.1-8b",
            "qwen-3-32b",
        ],
        emoji="🧠",
        enabled=False,  # Key in PROVIDER_ENV_VARS but no ProviderConfig yet — opt-in
    ),
    # ── Cloud: GitHub Copilot ─────────────────────────────────────────────────
    ProviderManifest(
        id="github_copilot",
        display_name="GitHub Copilot",
        description="GitHub Copilot API — OAuth-based, requires Copilot subscription.",
        tier="cloud",
        env_vars=["GITHUB_COPILOT_TOKEN"],
        vault_keys=["github_copilot/token"],
        auth_mode="oauth",
        models=["gpt-4o", "claude-3.5-sonnet"],
        emoji="🐙",
        enabled=False,  # Opt-in until full adapter is wired
    ),
    # ── Cloud: Kilocode ─────────────────────────────────────────────────
    ProviderManifest(
        id="kilocode",
        display_name="Kilocode",
        description="Kilo Code shared provider — OpenAI-compatible endpoint.",
        tier="cloud",
        env_vars=["KILOCODE_API_KEY"],
        vault_keys=["kilocode/api-key"],
        models=[],
        emoji="🔧",
        enabled=False,  # Opt-in
    ),
    # ── Cloud: Qwen (Alibaba) ───────────────────────────────────────────
    ProviderManifest(
        id="qwen",
        display_name="Qwen (Alibaba)",
        description="Qwen2.5 series via Alibaba Cloud — OAuth portal login.",
        tier="cloud",
        env_vars=["QWEN_API_KEY"],
        vault_keys=["qwen/api-key"],
        auth_mode="oauth",
        models=["qwen2.5-72b-instruct", "qwen2.5-coder-32b-instruct"],
        emoji="🟠",
        enabled=False,  # Opt-in
    ),
    # ── Proxy: BlockRun (x402 micropayments) ──────────────────────────────
    ProviderManifest(
        id="blockrun",
        display_name="BlockRun",
        description="Smart-routing AI proxy with x402 micropayments (Solana/USDC). "
        "Access 30+ models via one USDC wallet — no per-model API keys.",
        tier="proxy",
        env_vars=["BLOCKRUN_WALLET_KEY"],
        vault_keys=["blockrun/wallet-key"],
        requires_key=False,  # Proxy auto-generates wallet on first run
        models=[
            "claude-3.5-sonnet",
            "gpt-4o",
            "gpt-4o-mini",
            "deepseek-chat",
            "gemini-2.0-flash",
        ],
        emoji="⛓",
        enabled=False,  # Lab-derived — enable after x402 proxy integration is complete
    ),
    # ── Local: Ollama ─────────────────────────────────────────────────────────
    ProviderManifest(
        id="ollama",
        display_name="Ollama",
        description="Run any model locally (Llama, Mistral, Phi, Gemma…) — no API key.",
        tier="local",
        env_vars=[],
        vault_keys=[],
        requires_key=False,
        local_probe="127.0.0.1:11434",
        models=[],  # discovered dynamically via /api/tags
        emoji="🖥",
        auth_mode="none",
    ),
    # ── Local: LlamaCpp ───────────────────────────────────────────────────────
    ProviderManifest(
        id="llamacpp",
        display_name="llama.cpp",
        description="llama.cpp server — quantized GGUF models running locally.",
        tier="local",
        env_vars=[],
        vault_keys=[],
        requires_key=False,
        local_probe="127.0.0.1:8080",
        models=[],  # user-supplied GGUF
        emoji="🦙",
        auth_mode="none",
    ),
    # ── Local: AirLLM ─────────────────────────────────────────────────────────
    ProviderManifest(
        id="airllm",
        display_name="AirLLM",
        description="Run 70B+ models on a single GPU via layer-by-layer streaming.",
        tier="local",
        env_vars=["AIRLLM_MODEL_PATH"],
        vault_keys=[],
        requires_key=False,
        models=[
            "meta-llama/Llama-3.3-70B-Instruct",
            "Qwen/Qwen2.5-72B-Instruct",
            "deepseek-ai/deepseek-coder-33b-instruct",
        ],
        emoji="🌬",
        auth_mode="none",
    ),
    # ── Bridge: navig-bridge (VS Code Copilot) ─────────────────────────────
    ProviderManifest(
        id="mcp_bridge",
        display_name="Bridge",
        description="VS Code Copilot via navig-bridge MCP WebSocket — requires extension running.",
        tier="local",
        env_vars=[],
        vault_keys=["bridge/token"],
        requires_key=False,
        local_probe=f"127.0.0.1:{BRIDGE_DEFAULT_PORT}",
        models=["copilot-gpt-4o", "copilot-claude-3.5-sonnet"],
        emoji="⚡",
        auth_mode="token",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_INDEX: dict[str, ProviderManifest] = {p.id: p for p in ALL_PROVIDERS}


def get_provider(provider_id: str) -> ProviderManifest | None:
    """Return the manifest for *provider_id*, or ``None`` if not registered."""
    return _INDEX.get(provider_id)


def list_enabled_providers() -> list[ProviderManifest]:
    """Return all providers where ``enabled=True``, ordered: cloud → proxy → local."""
    _tier_order: dict[str, int] = {"cloud": 0, "proxy": 1, "local": 2}
    return sorted(
        [p for p in ALL_PROVIDERS if p.enabled],
        key=lambda p: (_tier_order.get(p.tier, 9), p.display_name.lower()),
    )


def list_all_providers() -> list[ProviderManifest]:
    """Return every registered provider regardless of enabled flag."""
    return list(ALL_PROVIDERS)
