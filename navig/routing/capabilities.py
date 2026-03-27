"""
Capability tags and model profiles for the unified router.

Every model declares a set of capability tags. The router maps each task
mode to required/preferred capabilities, then selects the best matching
model from the available provider.
"""

from __future__ import annotations

# ── Canonical capability tags ───────────────────────────────────────

CAPABILITY_TAGS = frozenset(
    {
        "fast",  # Low latency, streaming preferred
        "strong",  # Deep reasoning, high accuracy
        "coder",  # Code generation / editing
        "format_strict",  # High structured-output reliability (JSON, schemas)
        "tool_capable",  # Function calling, web search, citations
        "long_context",  # >32 k input tokens
    }
)

# ── Mode → capability requirements ─────────────────────────────────


class ModeProfile:
    """Capability requirements + cost/latency targets for a task mode."""

    __slots__ = ("required", "preferred", "cost_target", "latency_target")

    def __init__(
        self,
        required: set[str],
        preferred: set[str] | None = None,
        cost_target: str = "medium",
        latency_target: str = "medium",
    ):
        self.required = frozenset(required)
        self.preferred = frozenset(preferred or set())
        self.cost_target = cost_target
        self.latency_target = latency_target

    def score_model(self, model_caps: frozenset[str]) -> int:
        """Score a model against this profile. -1 = doesn't meet required."""
        if not self.required.issubset(model_caps):
            return -1
        return len(self.preferred & model_caps)


MODE_CAPABILITIES: dict[str, ModeProfile] = {
    "coding": ModeProfile(
        required={"coder"},
        preferred={"format_strict", "strong", "fast"},
        cost_target="low",
        latency_target="medium",
    ),
    "small_talk": ModeProfile(
        required={"fast"},
        preferred=set(),
        cost_target="minimal",
        latency_target="low",
    ),
    "big_tasks": ModeProfile(
        required={"strong"},
        preferred={"format_strict", "tool_capable", "long_context"},
        cost_target="high",
        latency_target="high",
    ),
    "summarize": ModeProfile(
        required={"fast"},
        preferred={"long_context"},
        cost_target="minimal",
        latency_target="low",
    ),
    "research": ModeProfile(
        required={"strong"},
        preferred={"tool_capable", "long_context"},
        cost_target="medium",
        latency_target="high",
    ),
}

# ── Known model capability databases (per provider) ────────────────
#
# VS Code Copilot models are dynamically discovered — the MCP server
# handles model selection based on purpose.  These tables are used
# only for daemon-side fallback providers.

OPENROUTER_MODELS: dict[str, frozenset[str]] = {
    "anthropic/claude-sonnet-4.5": frozenset({"fast", "strong", "coder", "format_strict"}),
    "anthropic/claude-sonnet-4": frozenset({"fast", "coder", "format_strict"}),
    # NOTE: claude-opus-4 is catalogued for capability lookup only.
    # It must NEVER appear as a routing default — too expensive for automated tasks.
    # Users may select it explicitly via CLI flag allow_premium=True.
    "anthropic/claude-opus-4": frozenset({"strong", "coder", "format_strict", "long_context"}),
    "openai/gpt-4o": frozenset({"fast", "coder", "format_strict", "tool_capable"}),
    "openai/gpt-4o-mini": frozenset({"fast", "format_strict", "tool_capable"}),
    "deepseek/deepseek-v3.2": frozenset({"fast", "coder", "long_context"}),
    "google/gemini-2.5-pro": frozenset({"strong", "long_context", "tool_capable"}),
    "google/gemini-2.5-flash": frozenset({"fast", "long_context"}),
    "qwen/qwen-72b": frozenset({"strong", "coder", "long_context"}),
}

GITHUB_MODELS: dict[str, frozenset[str]] = {
    "gpt-4o": frozenset({"fast", "coder", "format_strict", "tool_capable"}),
    "gpt-4o-mini": frozenset({"fast", "format_strict", "tool_capable"}),
    "Meta-Llama-3.1-405B-Instruct": frozenset({"strong", "long_context"}),
    "Mistral-large-2407": frozenset({"fast", "strong", "coder"}),
    "Mistral-Nemo": frozenset({"fast"}),
}

OLLAMA_MODELS: dict[str, frozenset[str]] = {
    "qwen2.5-coder:14b": frozenset({"fast", "coder"}),
    "qwen2.5-coder:7b": frozenset({"fast", "coder"}),
    "llama3.2": frozenset({"fast"}),
    "llama3.2:1b": frozenset({"fast"}),
    "deepseek-coder-v2:16b": frozenset({"coder", "fast"}),
    "codellama:13b": frozenset({"coder"}),
}

# ── Mode → preferred model per provider (fallback selection) ───────

MODE_MODEL_PREFERENCE: dict[str, dict[str, str]] = {
    "coding": {
        "openrouter": "anthropic/claude-sonnet-4.5",
        "github_models": "gpt-4o",
        "ollama": "qwen2.5-coder:14b",
    },
    "small_talk": {
        "openrouter": "openai/gpt-4o-mini",
        "github_models": "gpt-4o-mini",
        "ollama": "llama3.2",
    },
    "big_tasks": {
        # Intentionally Sonnet — Opus is 30× more expensive and must never be auto-selected.
        # To use Opus, set allow_premium=True on the RouteDecision explicitly.
        "openrouter": "anthropic/claude-sonnet-4.5",
        "github_models": "gpt-4o",
        "ollama": "",  # Not suitable for big tasks
    },
    "summarize": {
        "openrouter": "openai/gpt-4o-mini",
        "github_models": "gpt-4o-mini",
        "ollama": "llama3.2",
    },
    "research": {
        "openrouter": "google/gemini-2.5-pro",
        "github_models": "gpt-4o",
        "ollama": "",  # Not suitable for research
    },
}
