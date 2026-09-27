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
            # ⚠ gpt-4.1 stays FIRST deliberately: `models[0]` is the implicit
            # substitution default in model_router, so promoting gpt-5 here would
            # silently make every unknown-model slot fall back to a pricier model.
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
            # "o1-mini" removed 2026-09-08 — probe returned 404 (control: gpt-4.1 live).
            # GPT-5. Probe-verified live 2026-09-08 and only USABLE since the same
            # day: these reject `max_tokens` and any explicit `temperature`, so
            # every call 404'd/400'd until _sanitize_openai_body learned to send
            # `max_completion_tokens` and omit temperature for this family.
            # Listing them before that fix would have offered models navig could
            # not call — the o-series was in exactly that state for months.
            "gpt-5",
            "gpt-5-mini",
        ],
        emoji="🤖",
    ),
    # ── Cloud: Anthropic ──────────────────────────────────────────────────────
    ProviderManifest(
        id="anthropic",
        display_name="Anthropic",
        description="Claude Sonnet 4.6, Haiku 4.5, Opus 4.8 — extended thinking and long context.",
        tier="cloud",
        env_vars=["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
        vault_keys=["anthropic/api-key", "anthropic/api_key"],
        # ⚠ Audited 2026-09-26 through the operator's Claude SUBSCRIPTION: all
        # four ids previously here were gone (2x 404, 2x 410 EOL) — including
        # models[0], the credential probe and routing substitution default. The
        # first catalog sweep never saw it: it asked the API-key store, and a
        # subscription is an OAuth connection with no key.
        # Sonnet FIRST on purpose: models[0] is what model_router substitutes in,
        # and the routing table's own rule is that Opus is never auto-selected.
        models=[
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
            "claude-opus-4-8",
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
        # Audited 2026-09-08 against GET /v1/models (429 live) — 10 of the 23
        # listed ids had been withdrawn, INCLUDING the first entry, which is the
        # implicit substitution default. Spot-probed with controls:
        # anthropic/claude-3-7-sonnet 404, qwen/qwq-32b 404, openai/gpt-4.1 live.
        # For THIS provider the catalog is authoritative (it is the routing
        # table) — unlike xAI above, where it is not.
        models=[
            # Anthropic
            "anthropic/claude-sonnet-4.5",
            "anthropic/claude-opus-4.1",
            "anthropic/claude-haiku-4.5",
            # OpenAI
            "openai/gpt-5",
            "openai/gpt-4.1",
            "openai/gpt-4.1-mini",
            "openai/gpt-4o",
            "openai/gpt-4o-mini",
            "openai/o3-mini",
            # Google
            # Was the 05-06 PREVIEW id: it still answers, but OpenRouter no longer
            # lists it, so it has no published price (found 2026-09-27 by the audit).
            "google/gemini-2.5-pro",
            "google/gemini-2.5-flash",
            # Meta Llama
            "meta-llama/llama-3.3-70b-instruct",
            "meta-llama/llama-3.1-8b-instruct",
            # DeepSeek
            "deepseek/deepseek-chat-v3.1",
            "deepseek/deepseek-chat-v3-0324",
            "deepseek/deepseek-r1",
            # Mistral
            "mistralai/mistral-medium-3.1",
            "mistralai/mistral-small-3.1-24b-instruct",
            # Qwen
            "qwen/qwen3-235b-a22b",
            "qwen/qwen-2.5-72b-instruct",
            # Misc
            "microsoft/phi-4",
        ],
        emoji="🌐",
    ),
    # ── Cloud: Groq ───────────────────────────────────────────────────────────
    ProviderManifest(
        id="groq",
        display_name="Groq",
        description="Ultra-fast LPU inference — GPT-OSS, Qwen 3.8, Allam.",
        tier="cloud",
        env_vars=["GROQ_API_KEY"],
        vault_keys=["groq/api-key", "groq/api_key"],
        # ⚠ Audited 2026-09-26 by CALLING every id: **all ELEVEN shipped ids were
        # gone** — 4x 404 and 7x `400 has been decommissioned` — so groq's whole
        # catalog had rotated out from under this list, and `models[0]` (the
        # substitution default AND the credential probe) was dead. The five below
        # each answered a 1-token call; `whisper-large-v3` is in groq's own
        # listing and is NOT here because it answered "does not support chat" —
        # the listing is not the truth, the call is.
        models=[
            "openai/gpt-oss-120b",
            "openai/gpt-oss-20b",
            "openai/gpt-oss-safeguard-20b",
            "qwen/qwen3.8-27b",
            "allam-2-7b",
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
        # ⚠ NVIDIA NIM retires models aggressively and this list ROTS.
        # Audited 2026-09-08 against the live catalog and by a 1-token call to
        # each: ALL 20 previous entries were uncallable — 17 had vanished from
        # `GET /v1/models` entirely (four llama-3.1-* hit end-of-life on one day,
        # 2026-08-26) and the surviving three answered 404. The first entry is
        # the implicit default when a slot needs substituting, so a dead entry
        # here silently repointed every routing slot at a dead model.
        # ⚠ Presence in `/v1/models` is NOT sufficient — 4 of 8 catalogued
        # models 404'd on a real call. Re-audit by CALLING each one:
        #   POST https://integrate.api.nvidia.com/v1/chat/completions
        #   {"model": ..., "messages": [{"role":"user","content":"ping"}], "max_tokens": 1}
        # Every entry below returned 200. Warm latency in the comment.
        models=[
            # NVIDIA Nemotron — general purpose; first entry is the default
            "nvidia/nemotron-3-super-120b-a12b",  # 0.6s — big and the fastest
            "nvidia/nemotron-3.5-lightning-30b-a3b",  # 8.5s
            # Small / fast
            # "minimaxai/minimax-m3" — 410 EOL 2026-09-09, one day after it was added.
            # Code
            "poolside/laguna-xs-2.1",  # 0.5s
            # Others
            "moonshotai/kimi-k3",  # 11.6s
            "openai/gpt-oss-20b",  # 12.9s
            # ⚠ Flakiest entry here. Measured 2026-09-15, 8 probes: 5x 200, 1x 500,
            # 1x 502, 1x timeout. Alive, so listed — but not a good default, and
            # the probe retry is what keeps it from reading as broken.
            "mistralai/mistral-nemotron",  # 5.7s warm
        ],
        emoji="🟩",
    ),
    # ── Cloud: xAI / Grok ─────────────────────────────────────────────────────
    ProviderManifest(
        id="xai",
        display_name="xAI / Grok",
        description="Grok 4 and Grok 3 from xAI — real-time web access.",
        tier="cloud",
        env_vars=["XAI_API_KEY", "GROK_KEY"],
        vault_keys=["xai/api-key", "xai/api_key"],
        # Audited 2026-09-08 by CALLING each id (1 token). ⚠ For xAI the catalog
        # is NOT the truth: /v1/models lists only grok-4.x, yet every grok-3* id
        # below answers normally. Trusting the catalog here would have deleted
        # four working models. Probe; never infer from a listing.
        models=[
            "grok-4.6",  # live, and missing entirely before this audit
            "grok-4.5",
            "grok-4.3",
            "grok-3",
            "grok-3-fast",
            "grok-3-mini",
            "grok-3-mini-fast",
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
        enabled=False,  # Opt-in: has a ProviderConfig, but no key is expected by default
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
        enabled=False,  # Opt-in: has a ProviderConfig, but no key is expected by default
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
