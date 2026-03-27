"""
NAVIG Hybrid Model Router — Local small + Remote big + Coder-big

Routes every LLM request to the optimal model tier:

  small      Fast local model for chat / greetings / simple Q&A
  big        Powerful remote model for reasoning, planning, long-form
  coder_big  Code-optimised remote model for generation, review, patching

Routing modes (set via config):
  single               One model for everything (backward-compatible)
  rules_then_fallback  Deterministic heuristic rules, auto-fallback
  router_llm_json      Tiny LLM call classifies the request first

Config lives in ``~/.navig/config.yaml`` under ``ai.routing``.

When ``routing.enabled = false``, the entire module is a no-op and
NAVIG behaves exactly as its pre-router single-provider mode.

──────────────────────────────────────────────────────────────────────────────
ARCHITECTURE NOTE: Two-Layer LLM Routing
──────────────────────────────────────────────────────────────────────────────
THIS MODULE (Layer 2 — TIER routing) works alongside navig.llm_router
(Layer 1 — MODE routing). They are complementary, not competing:

  navig.llm_router            Layer 1: picks WHAT TO DO (mode: coding/chat/...)
  navig.agent.model_router ←  Layer 2: picks WHICH MODEL SIZE (small/big/coder)

agent/conversational.py is the correct orchestration point for both layers.
DO NOT merge these two modules.
──────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Data structures
# ═══════════════════════════════════════════════════════════════════


@dataclass
class RoutingDecision:
    """Output of the routing logic — tells the caller which model to use."""

    tier: str  # "small" | "big" | "coder_big"
    model: str  # e.g. "qwen2.5:3b-instruct"
    provider: str = ""  # e.g. "ollama", "openrouter"
    max_tokens: int = 512
    temperature: float = 0.7
    reason: str = ""
    overrides: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RoutingTelemetry:
    """Telemetry blob logged after every routed request."""

    selected_tier: str = ""
    routing_reason: str = ""
    max_tokens_used: int = 0
    latency_ms: int = 0
    fallback_occurred: bool = False
    fallback_reason: str = ""
    provider: str = ""
    model: str = ""
    user_override: str = ""  # "big", "small", "" if none


# ═══════════════════════════════════════════════════════════════════
# Model slot configuration
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ModelSlot:
    """One model tier slot (small / big / coder_big)."""

    provider: str = ""  # "ollama", "openrouter", "openai", "llamacpp"
    model: str = ""  # Model identifier
    max_tokens: int = 512
    temperature: float = 0.7
    num_ctx: int = 4096
    base_url: str = ""  # Override per-slot
    api_key: str = ""  # Override per-slot

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ModelSlot":
        defaults = d.get("defaults", {})
        return cls(
            provider=d.get("provider", ""),
            model=d.get("model", ""),
            max_tokens=int(defaults.get("max_tokens", d.get("max_tokens", 512))),
            temperature=float(defaults.get("temperature", d.get("temperature", 0.7))),
            num_ctx=int(defaults.get("num_ctx", d.get("num_ctx", 4096))),
            base_url=d.get("base_url", ""),
            api_key=d.get("api_key", ""),
        )


@dataclass
class RoutingConfig:
    """Full routing configuration."""

    enabled: bool = False
    mode: str = "rules_then_fallback"  # single | rules_then_fallback | router_llm_json
    prefer_local: bool = True
    fallback_enabled: bool = True

    small: ModelSlot = field(default_factory=ModelSlot)
    big: ModelSlot = field(default_factory=ModelSlot)
    coder_big: ModelSlot = field(default_factory=ModelSlot)

    # Legacy compat: flat accessors
    @property
    def small_model(self) -> str:
        return self.small.model

    @property
    def big_model(self) -> str:
        return self.big.model

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        default_model: str = "",
        global_cfg: Dict[str, Any] = None,
    ) -> "RoutingConfig":
        """
        Build from config dict.  Accepts both the new ``models`` schema
        and the legacy flat ``ai.routing`` keys.
        """
        global_cfg = global_cfg or {}
        cfg = cls()

        # ── Resolve enabled state ──
        # Legacy: mode != "single" implies enabled
        # New: explicit "enabled" key
        raw_mode = str(data.get("mode", "single")).lower()
        cfg.enabled = bool(data.get("enabled", raw_mode not in ("single", "")))
        cfg.mode = _normalize_mode(raw_mode)
        cfg.prefer_local = bool(data.get("prefer_local", True))
        cfg.fallback_enabled = bool(data.get("fallback_enabled", True))

        # Resolve API key: per-slot → env → global config
        default_api_key = global_cfg.get("openrouter_api_key", "") or os.environ.get(
            "OPENROUTER_API_KEY", ""
        )

        # ── New schema (models.small / models.big / models.coder_big) ──
        models_block = data.get("models", {})
        if models_block:
            cfg.small = ModelSlot.from_dict(models_block.get("small", {}))
            cfg.big = ModelSlot.from_dict(models_block.get("big", {}))
            cb = models_block.get("coder_big", models_block.get("coder", {}))
            cfg.coder_big = ModelSlot.from_dict(cb)
        else:
            # ── Legacy flat keys (backward compat) ──
            cfg.small = ModelSlot(
                provider=data.get("small_provider", "ollama"),
                model=data.get("small_model", ""),
                max_tokens=int(data.get("small_max_tokens", 200)),
                temperature=float(data.get("small_temperature", 0.6)),
                num_ctx=int(data.get("small_ctx", 2048)),
            )
            cfg.big = ModelSlot(
                provider=data.get("big_provider", "ollama"),
                model=data.get("big_model", default_model),
                max_tokens=int(data.get("big_max_tokens", 800)),
                temperature=float(data.get("big_temperature", 0.7)),
                num_ctx=int(data.get("big_ctx", 4096)),
            )
            # Legacy had no coder_big — alias to big
            cfg.coder_big = ModelSlot(
                provider=cfg.big.provider,
                model=cfg.big.model,
                max_tokens=cfg.big.max_tokens,
                temperature=max(cfg.big.temperature - 0.1, 0.1),
                num_ctx=cfg.big.num_ctx,
            )

        # Inject default API key where not overridden
        for slot in (cfg.small, cfg.big, cfg.coder_big):
            if slot.provider in ("openrouter", "openai") and not slot.api_key:
                if slot.provider == "openrouter":
                    slot.api_key = default_api_key
                elif slot.provider == "openai":
                    slot.api_key = (
                        os.environ.get("OPENAI_API_KEY", "") or default_api_key
                    )

            # GitHub Models — resolve token from vault → config → env
            if slot.provider == "github_models" and not slot.api_key:
                gh_token = ""
                # 1. Vault
                try:
                    from navig.vault import get_vault

                    vault = get_vault()
                    secret = vault.get_secret(
                        "github_models", "token", caller="model_router"
                    )
                    if secret:
                        gh_token = secret
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
                # 2. Config
                if not gh_token:
                    gh_token = global_cfg.get("github_models", {}).get("token", "")
                # 3. Environment
                if not gh_token:
                    gh_token = os.environ.get("GITHUB_TOKEN", "")
                if gh_token:
                    slot.api_key = gh_token

        # Validate
        if cfg.enabled and cfg.mode != "single":
            if not cfg.small.model:
                logger.warning("Routing enabled but small.model is empty → disabling")
                cfg.enabled = False

        return cfg

    @property
    def is_active(self) -> bool:
        return self.enabled and self.mode != "single"

    def slot_for_tier(self, tier: str) -> ModelSlot:
        return {"small": self.small, "big": self.big, "coder_big": self.coder_big}.get(
            tier, self.big
        )


def _normalize_mode(raw: str) -> str:
    """Map legacy mode names to new canonical names."""
    mapping = {
        "heuristic": "rules_then_fallback",
        "router": "router_llm_json",
        "rules": "rules_then_fallback",
    }
    return mapping.get(raw, raw)


# ═══════════════════════════════════════════════════════════════════
# Heuristic rule-based router  (zero LLM calls, 3 tiers)
# ═══════════════════════════════════════════════════════════════════

# ── Code-detection patterns ──
_CODE_FENCE = re.compile(r"```")
_STACK_TRACE = re.compile(
    r"(Traceback \(most recent|File \".+\", line \d+|"
    r"at .+\.(?:js|ts|py|java|go|rb|rs):\d+|"
    r"Exception in thread|SyntaxError|TypeError|ValueError|"
    r"FATAL ERROR|panic:|error\[E\d+\])",
    re.IGNORECASE,
)
_DIFF_FRAG = re.compile(r"^[+-]{3} [ab]/|^@@ -\d+", re.MULTILINE)
_FILE_PATH_RE = re.compile(r"[/\\][\w.\-]+(?:[/\\][\w.\-]+){1,}")

_CODER_KEYWORDS = re.compile(
    r"\b("
    r"refactor|PR\b|pull\s*request|review\s+(this\s+)?code|"
    r"bug|patch|fix\s*(?:this\s+)?(?:code|error|bug|issue)?|"
    r"write\s+(?:\w+\s+){0,3}(function|class|module|script|test|endpoint|api|query|css|html|ui)|"
    r"implement|unit\s*test|integration\s*test|lint|format|"
    r"compile|runtime\s+error|syntax\s+error|type\s+error|"
    r"debug|traceback|exception|stack\s*trace|panic|sigsegv|segfault|"
    r"code|coding|programming|algorithm|snippet|"
    r"python|golang|java|rust|typescript|javascript|react|vue|svelte|node\.js|next\.js|sql|postgres|bash|shell|"
    r"git\s+(commit|push|pull|rebase|merge|status|log)|"
    r"docker|dockerfile|k8s|kubernetes|aws|gcp|azure|"
    r"regex|regular\s+expression|"
    r"исправь|код|функци|тест|ошибк|баг|скрипт"
    r")\b",
    re.IGNORECASE,
)

# ── Big-model (reasoning/planning) patterns ──
_BIG_KEYWORDS = re.compile(
    r"\b("
    r"plan|architect(?:ure)?|design|strategy|compare|deep|comprehensive|"
    r"analyze|analyse|step[\s-]by[\s-]step|detailed|complete|"
    r"explain|elaborate|summarize|summary|specification|spec|"
    r"multi[\s-]?step|orchestrat(?:e|ion)|deploy(?:ment)?\s+plan|migration\s+plan|"
    r"brainstorm|suggest|idea|outline|draft|evaluate|audit|optimize|optimization|"
    r"tutorial|guide|overview|pros\s+and\s+cons|trade-off|tradeoffs?|breakdown|"
    r"why|how\s+to|what\s+is\s+the\s+difference|"
    r"подробно|пошагов|объясни|стратеги|спроектируй|сравни|"
    r"详细|一步步|设计|分析|策略|为什么"
    r")\b",
    re.IGNORECASE,
)
_STRUCTURED_OUTPUT = re.compile(
    r"\b(table|json\s*schema|specification|markdown\s*table|csv|yaml)\b",
    re.IGNORECASE,
)

_BIG_LENGTH_THRESHOLD = 350


def heuristic_route(message: str, cfg: RoutingConfig) -> RoutingDecision:
    """
    Pure-function heuristic routing.  Zero LLM calls.

    Priority order:
      1. Code fences / stack traces / diffs / code keywords → coder_big
      2. Planning / reasoning / structured output keywords   → big
      3. Long message (>350 chars) without code              → big
      4. Everything else                                     → small
    """
    reasons: List[str] = []

    # ── 1. Coder detection ──
    has_code = False
    if _CODE_FENCE.search(message):
        reasons.append("code_fence")
        has_code = True
    if _STACK_TRACE.search(message):
        reasons.append("stack_trace")
        has_code = True
    if _DIFF_FRAG.search(message):
        reasons.append("diff_fragment")
        has_code = True
    ckw = _CODER_KEYWORDS.search(message)
    if ckw:
        reasons.append(f"code_kw:{ckw.group(0)[:20]}")
        has_code = True

    if has_code:
        slot = cfg.coder_big
        return RoutingDecision(
            tier="coder_big",
            model=slot.model,
            provider=slot.provider,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            reason="; ".join(reasons),
        )

    # ── 2. Big-model reasoning / planning ──
    bkw = _BIG_KEYWORDS.search(message)
    if bkw:
        reasons.append(f"reasoning_kw:{bkw.group(0)[:20]}")
    if _STRUCTURED_OUTPUT.search(message):
        reasons.append("structured_output")
    if _FILE_PATH_RE.search(message) and not has_code:
        reasons.append("file_path")

    if reasons:
        slot = cfg.big
        return RoutingDecision(
            tier="big",
            model=slot.model,
            provider=slot.provider,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            reason="; ".join(reasons),
        )

    # ── 3. Length heuristic ──
    if len(message) > _BIG_LENGTH_THRESHOLD:
        slot = cfg.big
        return RoutingDecision(
            tier="big",
            model=slot.model,
            provider=slot.provider,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            reason=f"long_msg:{len(message)}",
        )

    # ── 4. Default: small ──
    slot = cfg.small
    return RoutingDecision(
        tier="small",
        model=slot.model,
        provider=slot.provider,
        max_tokens=slot.max_tokens,
        temperature=slot.temperature,
        reason="simple_chat",
    )


# ═══════════════════════════════════════════════════════════════════
# LLM-based router (optional, 3-tier)
# ═══════════════════════════════════════════════════════════════════

_ROUTER_SYSTEM_PROMPT = """\
You are a routing controller. Return ONLY valid JSON.

Three tiers:
- small: fast chat, greetings, simple factual Q&A, light reasoning
- big: deep reasoning, planning, long-form, multi-step, structured output
- coder_big: any code task — generation, review, debugging, patching, diffs

Rules:
- Code fences, stack traces, code keywords → coder_big
- Planning, design, strategy, detailed explanations → big
- Simple conversational messages → small
- When uncertain → big

Output:
{"tier":"small|big|coder_big","max_tokens":180,"temperature":0.6,"notes":"reason"}
"""


async def llm_route(
    message: str,
    cfg: RoutingConfig,
    chat_fn: Callable,
) -> RoutingDecision:
    """Use the small model to classify the request (router-LLM JSON mode)."""
    try:
        raw = await chat_fn(
            model=cfg.small.model,
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM_PROMPT},
                {"role": "user", "content": message[:500]},
            ],
            temperature=0.0,
            max_tokens=100,
        )
        m = re.search(r"\{[^}]+\}", raw if isinstance(raw, str) else raw.content)
        if not m:
            raise ValueError("No JSON in router response")
        d = json.loads(m.group(0))
        tier = d.get("tier", d.get("model", "big"))
        if tier not in ("small", "big", "coder_big"):
            tier = "big"
        slot = cfg.slot_for_tier(tier)
        return RoutingDecision(
            tier=tier,
            model=slot.model,
            provider=slot.provider,
            max_tokens=d.get("max_tokens", slot.max_tokens),
            temperature=d.get("temperature", slot.temperature),
            reason=d.get("notes", "router_llm"),
        )
    except Exception as exc:
        logger.warning("Router LLM failed (%s) → heuristic fallback", exc)
        return heuristic_route(message, cfg)


# ═══════════════════════════════════════════════════════════════════
# Fallback quality check  (max 1 retry)
# ═══════════════════════════════════════════════════════════════════

_LOW_CONFIDENCE_PATTERNS = re.compile(
    r"("
    r"i'?m not sure|i cannot|i can'?t|i don'?t know|"
    r"i don'?t have the ability|as an ai|beyond my|"
    r"unable to|i apologize|sorry.{0,10}(can't|cannot)|"
    r"не уверен|не могу(?! не)|не знаю|не способен|извини|"
    r"无法|不确定|抱歉|我不能"
    r")",
    re.IGNORECASE,
)

_HEDGE_PATTERNS = re.compile(
    r"("
    r"(?:it'?s? )?(?:possible|might|maybe|perhaps|could be)\b.*\b(?:but|however|although)|"
    r"i(?:'m| am) not (?:entirely |fully )?(?:certain|confident)"
    r")",
    re.IGNORECASE,
)


def needs_fallback(response: str, tier: str) -> bool:
    """True if a small model response should be retried.  Only for tier==small."""
    if tier != "small":
        return False
    if not response or len(response.strip()) < 10:
        return True
    if _LOW_CONFIDENCE_PATTERNS.search(response):
        return True
    if _HEDGE_PATTERNS.search(response):
        return True
    return False


def pick_fallback_tier(original_tier: str, message: str) -> str:
    """Choose which tier to escalate to on fallback."""
    if _CODE_FENCE.search(message) or _CODER_KEYWORDS.search(message):
        return "coder_big"
    return "big"


FALLBACK_NOTE = "Previous attempt was insufficient. Answer fully and completely now."


# ═══════════════════════════════════════════════════════════════════
# Main hybrid router class
# ═══════════════════════════════════════════════════════════════════


class HybridRouter:
    """
    Stateful router — holds config, provider pool, and exposes
    ``route()`` / ``call()`` for the AI client.

    Usage::

        router = HybridRouter.from_config(navig_config_dict)
        decision = router.route(user_message, tier_override="big")
        response = await router.call(messages, decision)
    """

    def __init__(self, cfg: RoutingConfig):
        self.cfg = cfg
        self._providers: Dict[str, Any] = {}  # lazy-cached per provider key

    @classmethod
    def from_config(
        cls,
        global_config: Dict[str, Any],
        default_model: str = "",
    ) -> "HybridRouter":
        """Build a HybridRouter from the merged NAVIG config dict."""
        ai_cfg = global_config.get("ai", {})
        routing_raw = ai_cfg.get("routing", {})
        if not routing_raw:
            routing_raw = global_config.get("ai_routing", {})

        # Merge ai-level keys into routing dict for backward compat
        merged = dict(routing_raw)
        if "models" in ai_cfg:
            merged["models"] = ai_cfg["models"]

        cfg = RoutingConfig.from_dict(
            merged, default_model=default_model, global_cfg=global_config
        )
        return cls(cfg)

    # ── Provider pool ──

    def _get_provider(self, slot: ModelSlot):
        """Lazy-create a provider instance for the given slot."""
        key = f"{slot.provider}:{slot.base_url or 'default'}"
        if key not in self._providers:
            try:
                from navig.agent.llm_providers import create_provider
            except ImportError:
                logger.error("llm_providers module not available")
                return None
            kwargs: Dict[str, Any] = {}
            if slot.base_url:
                kwargs["base_url"] = slot.base_url
            if slot.api_key:
                kwargs["api_key"] = slot.api_key
            self._providers[key] = create_provider(slot.provider, **kwargs)
        return self._providers[key]

    # ── Public API ──

    @property
    def is_active(self) -> bool:
        return self.cfg.is_active

    def route(self, user_message: str, tier_override: str = "") -> RoutingDecision:
        """
        Synchronous rule-based routing (zero LLM calls).

        ``tier_override``: "small", "big", "coder_big" — skips rules entirely.
        """
        if tier_override:
            slot = self.cfg.slot_for_tier(tier_override)
            return RoutingDecision(
                tier=tier_override,
                model=slot.model,
                provider=slot.provider,
                max_tokens=slot.max_tokens,
                temperature=slot.temperature,
                reason=f"user_override:{tier_override}",
            )

        if not self.is_active:
            slot = self.cfg.big if self.cfg.big.model else self.cfg.small
            tier = "big" if self.cfg.big.model else "small"
            return RoutingDecision(
                tier=tier,
                model=slot.model,
                provider=slot.provider,
                max_tokens=slot.max_tokens,
                temperature=slot.temperature,
                reason="single_mode",
            )

        return heuristic_route(user_message, self.cfg)

    async def route_async(
        self,
        user_message: str,
        tier_override: str = "",
        chat_fn: Callable = None,
    ) -> RoutingDecision:
        """Async routing — uses LLM router if mode == router_llm_json."""
        if tier_override or not self.is_active:
            return self.route(user_message, tier_override)
        if self.cfg.mode == "router_llm_json" and chat_fn:
            return await llm_route(user_message, self.cfg, chat_fn)
        return heuristic_route(user_message, self.cfg)

    async def call(
        self,
        messages: List[Dict[str, str]],
        decision: RoutingDecision,
    ):
        """
        Execute the LLM call via the provider from ``decision``.
        Returns an ``LLMResponse`` (from llm_providers).
        """
        slot = self.cfg.slot_for_tier(decision.tier)
        provider = self._get_provider(slot)
        if provider is None:
            raise RuntimeError(
                f"No provider available for tier={decision.tier} provider={slot.provider}"
            )

        kwargs = {}
        if slot.provider == "ollama" and slot.num_ctx:
            kwargs["num_ctx"] = slot.num_ctx

        return await provider.chat(
            model=decision.model,
            messages=messages,
            temperature=decision.temperature,
            max_tokens=decision.max_tokens,
            **kwargs,
        )

    def should_fallback(self, response_text: str, decision: RoutingDecision) -> bool:
        """Check if small-model output should be retried."""
        if not self.cfg.fallback_enabled:
            return False
        return needs_fallback(response_text, decision.tier)

    def fallback_decision(
        self, original: RoutingDecision, user_message: str = ""
    ) -> RoutingDecision:
        """Create a fallback decision (small → big or coder_big)."""
        fb_tier = pick_fallback_tier(original.tier, user_message)
        slot = self.cfg.slot_for_tier(fb_tier)
        return RoutingDecision(
            tier=fb_tier,
            model=slot.model,
            provider=slot.provider,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            reason=f"fallback_from_{original.tier}",
        )

    # Legacy compat
    def big_decision(self) -> RoutingDecision:
        """Force a big-model decision (used for fallback retry)."""
        slot = self.cfg.big
        return RoutingDecision(
            tier="big",
            model=slot.model,
            provider=slot.provider,
            max_tokens=slot.max_tokens,
            temperature=slot.temperature,
            reason="fallback_from_small",
        )

    async def close(self):
        """Close all cached provider sessions."""
        for p in self._providers.values():
            try:
                await p.close()
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        self._providers.clear()

    # ── Diagnostics ──

    def status_summary(self) -> Dict[str, Any]:
        """Return a dict for /status endpoint and logs."""
        return {
            "enabled": self.cfg.enabled,
            "mode": self.cfg.mode,
            "active": self.is_active,
            "prefer_local": self.cfg.prefer_local,
            "fallback_enabled": self.cfg.fallback_enabled,
            "models": {
                "small": {
                    "provider": self.cfg.small.provider,
                    "model": self.cfg.small.model,
                },
                "big": {"provider": self.cfg.big.provider, "model": self.cfg.big.model},
                "coder_big": {
                    "provider": self.cfg.coder_big.provider,
                    "model": self.cfg.coder_big.model,
                },
            },
        }

    def models_table(self) -> str:
        """
        Human-readable table of all configured model slots.
        Used for Telegram /models command and /status display.
        """
        lines = [
            "┌─────────┬────────────┬─────────────────────────────────┬────────┐",
            "│  Tier   │  Provider  │  Model                          │ Tokens │",
            "├─────────┼────────────┼─────────────────────────────────┼────────┤",
        ]
        for tier, slot in [
            ("small", self.cfg.small),
            ("big", self.cfg.big),
            ("coder", self.cfg.coder_big),
        ]:
            prov = (slot.provider or "—")[:10].ljust(10)
            mdl = (slot.model or "—")[:33].ljust(33)
            tok = str(slot.max_tokens).rjust(6)
            lines.append(f"│ {tier:<7} │ {prov} │ {mdl} │ {tok} │")
        lines.append(
            "└─────────┴────────────┴─────────────────────────────────┴────────┘"
        )
        return "\n".join(lines)


# Backward-compatibility alias — old code imports ``ModelRouter``
ModelRouter = HybridRouter
