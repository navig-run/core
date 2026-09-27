"""
navig.agent.usage_tracker — Per-session LLM cost and token accounting.

Tracks token usage and estimated USD cost for every LLM call inside an
agentic session.  The cost summary is surfaced to the user at the end of
each :meth:`ConversationalAgent.run_agentic` call.

Usage::

    from navig.agent.usage_tracker import CostTracker, UsageEvent

    tracker = CostTracker()
    tracker.record(UsageEvent(
        turn=1, model="gpt-4o", provider="openai",
        prompt_tokens=1500, completion_tokens=300
    ))
    print(tracker.session_cost().summary_str())
    # "1 turn · 1,800 tok · $0.0120"
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Pricing table  (USD per 1,000,000 tokens)
# Format: {model_name: (input_per_M, output_per_M, cache_read_per_M, cache_write_per_M)}
#
# ⚠ These are FACTS about someone else's price list, and they rot. Audited
# 2026-09-27 against each provider's own pricing page (standard tier, shortest
# context band): OpenAI developers.openai.com/api/docs/pricing · Anthropic
# platform.claude.com/docs/en/about-claude/pricing · xAI docs.x.ai/docs/models ·
# Google ai.google.dev/gemini-api/docs/pricing. That audit found o3 billed 5x
# too high, Opus 4.5 3x, Gemini 2.5 Flash's output 8x too LOW, and the provider
# defaults grok-4.6 and the Claude 5 family not priced at all (so $0.00).
# Entries NOT on a current page (grok-3*, Mistral, gemini-1.5*) were left as
# they were — unverified, not confirmed. Re-audit by opening those pages.
# ─────────────────────────────────────────────────────────────

PRICE_TABLE: dict[str, tuple[float, float, float, float]] = {
    # OpenAI
    # GPT-4.1 family — OpenAI published pricing at launch (2025-04-14). These
    # were priced as classic "gpt-4" by the old prefix scan; gpt-4.1 is
    # OpenAI's catalog head (the credential probe and substitution default).
    # GPT-5 base models — in the openai manifest, live, and unpriced until
    # 2026-09-27 (developers.openai.com). gpt-5-nano is deliberately absent:
    # the size-suffix rule keeps it from inheriting gpt-5's price.
    "gpt-5":                     (1.25,  10.00, 0.125, 0.00),
    "gpt-5-mini":                (0.25,   2.00, 0.025, 0.00),
    "gpt-4.1":                   (2.00,   8.00, 0.50,  0.00),
    "gpt-4.1-mini":              (0.40,   1.60, 0.10,  0.00),
    "gpt-4.1-nano":              (0.10,   0.40, 0.025, 0.00),
    "gpt-4o":                    (2.50,  10.00, 1.25,  0.00),
    "gpt-4o-mini":               (0.15,   0.60, 0.075, 0.00),
    "gpt-4-turbo":               (10.00,  30.00, 0.00, 0.00),
    "gpt-4":                     (30.00,  60.00, 0.00, 0.00),
    "gpt-3.5-turbo":             (0.50,   1.50,  0.00, 0.00),
    "o1":                        (15.00,  60.00, 7.50, 0.00),
    "o1-mini":                   (3.00,   12.00, 0.00, 0.00),
    "o3":                        (2.00,    8.00, 0.50, 0.00),   # was 10/40 — the pre-cut price
    "o3-mini":                   (1.10,   4.40,  0.55, 0.00),
    "o4-mini":                   (1.10,   4.40,  0.275, 0.00),
    # Anthropic Claude — current. (Order no longer matters: the longest priced
    # key wins, so "claude-opus-4-8" can never fall through to "claude-opus-4".)
    # Claude 5 family — none was priced, so every turn on them read $0.00.
    # Opus 5.5's cache hit is 0.05x input, not the usual 0.1x (per the page).
    "claude-fable-5-1":          (10.00,  50.00, 0.25, 12.50),
    "claude-fable-5":            (10.00,  50.00, 1.00, 12.50),
    "claude-opus-5-5":           (4.00,   20.00, 0.20,  5.00),
    "claude-opus-5":             (5.00,   25.00, 0.50,  6.25),
    "claude-sonnet-5":           (2.00,   10.00, 0.20,  2.50),
    "claude-opus-4-8":           (5.00,   25.00, 0.50,  6.25),
    "claude-opus-4-7":           (5.00,   25.00, 0.50,  6.25),
    "claude-opus-4-6":           (5.00,   25.00, 0.50,  6.25),
    "claude-sonnet-4-6":         (3.00,   15.00, 0.30,  3.75),
    "claude-haiku-4-5":          (1.00,    5.00, 0.10,  1.25),
    # Anthropic Claude — legacy
    "claude-opus-4-5":           (5.00,   25.00, 0.50,  6.25),   # was 15/75 — that is Opus 4 / 4.1
    "claude-opus-4":             (15.00,  75.00, 1.50, 18.75),
    "claude-sonnet-4-5":         (3.00,   15.00, 0.30,  3.75),
    "claude-sonnet-4":           (3.00,   15.00, 0.30,  3.75),
    "claude-3-5-sonnet-20241022":(3.00,   15.00, 0.30,  3.75),
    "claude-3-5-haiku-20241022": (0.80,   4.00,  0.08,  1.00),
    "claude-3-opus-20240229":    (15.00,  75.00, 1.50, 18.75),
    "claude-3-haiku-20240307":   (0.25,   1.25,  0.03,  0.30),
    # Google Gemini
    "gemini-2.5-pro":            (1.25,  10.00,  0.125, 0.00),  # output was 5.00 (half)
    "gemini-2.5-flash":          (0.30,   2.50,  0.03,  0.00),  # was 0.075/0.30 — 1.5-Flash's
    "gemini-1.5-pro":            (1.25,   5.00,  0.00,  0.00),
    "gemini-1.5-flash":          (0.075,  0.30,  0.00,  0.00),
    # Nous Research (via OpenRouter)
    "hermes-3-70b":              (0.70,   0.80,  0.00,  0.00),
    "hermes-3-405b":             (1.79,   1.79,  0.00,  0.00),
    # Mistral
    "mistral-large-latest":      (3.00,   9.00,  0.00,  0.00),
    "mistral-small-latest":      (1.00,   3.00,  0.00,  0.00),
    # xAI Grok (published per-M pricing). grok-3-mini is the fast-chat /
    # heartbeat default, so it's worth tracking accurately instead of $0.00.
    # grok-4.6 is xAI's catalog head (credential probe + substitution default)
    # and was unpriced. Short-prompt tier; long prompts cost 2x.
    "grok-4.6":                  (2.00,   6.00,  0.50,  0.00),
    "grok-4.5":                  (2.00,   6.00,  0.30,  0.00),
    "grok-4.3":                  (1.25,   2.50,  0.20,  0.00),
    "grok-3-mini":               (0.30,   0.50,  0.00,  0.00),
    "grok-3":                    (3.00,  15.00,  0.00,  0.00),
    "grok-2":                    (2.00,  10.00,  0.00,  0.00),
    "grok-beta":                 (5.00,  15.00,  0.00,  0.00),
    # NVIDIA NIM (build.nvidia.com) — free tier; explicit $0 silences the
    # "no pricing info" probe for the configured small/big/coder models.
    "meta/llama-3.1-8b-instruct":   (0.00, 0.00, 0.00, 0.00),
    "meta/llama-3.3-70b-instruct":  (0.00, 0.00, 0.00, 0.00),
    "qwen/qwen3-coder-480b-a35b-instruct": (0.00, 0.00, 0.00, 0.00),
}


#: What may follow a priced key for a model id to be a VARIANT of it — a dated
#: snapshot (``-20241022``), a preview/speed suffix (``-preview-05-06``,
#: ``-fast``), a Vertex version (``@20250514``), an ollama tag (``:8b``).
#: ⚠ ``.`` is deliberately absent: it CONTINUES a version number, so ``gpt-4``
#: must not price ``gpt-4.1``.
_VARIANT_SEPARATORS = ("-", "@", ":")
#: A SIZE suffix after a priced key names a different, cheaper model, not a
#: variant: `gemini-2.5-flash-lite` is $0.10/$0.40 against Flash's $0.30/$2.50,
#: `gpt-5-nano` is a fraction of `gpt-5`. Inheriting the bigger model's price
#: over-bills it several times over, so these stay unpriced until listed.
_SIZE_SUFFIXES = ("mini", "nano", "lite", "small", "tiny", "micro")


def model_price_key(model: str, keys) -> str | None:
    """The priced key that *model* is, or is a variant of — else None.

    Exact match first; otherwise the LONGEST key such that *model* is that key
    followed by a variant separator. Two rules the old scan broke:

    * **Longest wins, not dict order.** A first-match scan made correctness
      depend on declaration order — this table carried a comment warning that
      exact entries "must precede" a shorter prefix, which is a rule written
      down because nothing enforced it.
    * **A version continues past ``.``.** ``"gpt-4.1".startswith("gpt-4")`` is
      True, so the whole GPT-4.1 family — ``gpt-4.1-nano`` included, the
      cheapest OpenAI model, and ``gpt-4.1`` itself, OpenAI's catalog head —
      was billed at classic GPT-4's $30/$60 per M, roughly 15× too high.

    The old REVERSE match (a key that starts with the model: ``"gpt-4"`` →
    ``"gpt-4o"``) is gone too — it priced an unspecified model as some specific
    other one.
    """
    if model in keys:
        return model
    best: str | None = None
    for key in keys:
        if len(key) < len(model) and model.startswith(key) and model[len(key)] in _VARIANT_SEPARATORS:
            # ANY size token in the remainder, not just the first: otherwise a
            # shorter key reclaims what a longer one refused — measured,
            # `claude-opus-4-8-mini` fell through to `claude-opus-4` via "-8-mini".
            if any(tok in _SIZE_SUFFIXES for tok in model[len(key) + 1:].split("-")):
                continue
            if best is None or len(key) > len(best):
                best = key
    return best


def _lookup_price(model: str, provider: str | None = None) -> tuple[float, float, float, float]:
    """Return (input_per_M, output_per_M, cache_read_per_M, cache_write_per_M) for *model*.

    On OpenRouter the price comes from OpenRouter's own published list (cached
    by ``navig.agent.openrouter_prices``) — its ids (``anthropic/claude-sonnet-4.5``)
    match no table entry, so every OpenRouter turn used to read $0.00. The
    PROVIDER decides it, not the id's shape: ``openai/gpt-oss-20b`` is also an
    NVIDIA and a groq id, and those are free tiers.

    Otherwise see :func:`model_price_key`. Returns zeros for unknown models, and
    says so at debug level.
    """
    if (provider or "").lower() == "openrouter":
        try:
            from navig.agent import openrouter_prices

            hit = openrouter_prices.lookup(model)
        except Exception:  # noqa: BLE001 — a cost lookup must never break a turn
            hit = None
        if hit is not None:
            return hit
    key = model_price_key(model, PRICE_TABLE)
    if key is not None:
        return PRICE_TABLE[key]

    logger.debug("No pricing info for model %r — cost will show as $0.00", model)
    return (0.0, 0.0, 0.0, 0.0)


# ─────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────


@dataclass
class UsageEvent:
    """Token usage from a single LLM call.

    Attributes:
        turn:               Sequential turn number within the session.
        model:              Model name (e.g. ``"gpt-4o"``).
        provider:           Provider name (e.g. ``"openai"``).
        prompt_tokens:      Number of input tokens.
        completion_tokens:  Number of output tokens.
        cache_read_tokens:  Tokens served from prompt cache (Anthropic).
        cache_write_tokens: Tokens written to prompt cache (Anthropic).
        timestamp:          Wall-clock time of the call.
        metadata:           Optional extra info (e.g. mode, tier, session_id).
    """

    turn: int
    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Sum of prompt + completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    def cost_usd(self) -> float:
        """Estimated USD cost for this event."""
        inp, out, cache_r, cache_w = _lookup_price(self.model, self.provider)
        cost = (
            self.prompt_tokens * inp / 1_000_000
            + self.completion_tokens * out / 1_000_000
            + self.cache_read_tokens * cache_r / 1_000_000
            + self.cache_write_tokens * cache_w / 1_000_000
        )
        return cost


@dataclass
class SessionCost:
    """Aggregated cost and token usage for an entire agentic session.

    Attributes:
        total_usd:   Estimated total USD cost.
        total_tokens: Sum of all prompt + completion tokens.
        events:       Individual :class:`UsageEvent` records.
    """

    total_usd: float
    total_tokens: int
    events: list[UsageEvent] = field(default_factory=list)

    def summary_str(self) -> str:
        """One-line human-readable cost summary.

        Example: ``"3 turns · 12,450 tok · $0.023"``
        """
        turn_count = len(self.events)
        turns_label = f"{turn_count} turn{'s' if turn_count != 1 else ''}"
        tok_label = f"{self.total_tokens:,} tok"
        cost_label = f"${self.total_usd:.4f}"
        return f"{turns_label} · {tok_label} · {cost_label}"

    def detailed_str(self) -> str:
        """Multi-line detailed breakdown per turn."""
        if not self.events:
            return "No LLM calls recorded."
        lines = [f"Session cost: {self.summary_str()}"]
        for ev in self.events:
            lines.append(
                f"  Turn {ev.turn}: {ev.model} | "
                f"in={ev.prompt_tokens:,} out={ev.completion_tokens:,} "
                f"(cache_r={ev.cache_read_tokens:,} cache_w={ev.cache_write_tokens:,}) "
                f"= ${ev.cost_usd():.5f}"
            )
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# CostTracker
# ─────────────────────────────────────────────────────────────


def _last_turn_path() -> "Path":
    from navig.platform.paths import config_dir  # noqa: PLC0415

    return config_dir() / "perf" / "last_turn.json"


def record_last_turn(event: UsageEvent) -> None:
    """Snapshot the most recent LLM call to ``<config_dir>/perf/last_turn.json``.

    ``CostTracker`` is per-``run_agentic``-call and in-memory, so the CLI can
    never see what the *daemon* just did — which is exactly the number an
    operator needs to answer "is my prompt actually being cached?".
    ``navig agent context`` reads this back.

    Best-effort in every direction (mirrors the ``perf/config_incidents.jsonl``
    precedent): a telemetry note must never raise into a live turn.
    """
    try:
        path = _last_turn_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "turn": event.turn,
            "model": event.model,
            "provider": event.provider,
            "prompt_tokens": event.prompt_tokens,
            "completion_tokens": event.completion_tokens,
            "cache_read_tokens": event.cache_read_tokens,
            "cache_write_tokens": event.cache_write_tokens,
            "cost_usd": round(event.cost_usd(), 6),
            "at": event.timestamp.isoformat(),
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:  # noqa: BLE001 — never break a turn for telemetry
        logger.debug("record_last_turn skipped: %s", exc)


def read_last_turn() -> dict[str, Any] | None:
    """Read back the last recorded turn, or ``None`` when absent/unreadable."""
    try:
        path = _last_turn_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("read_last_turn skipped: %s", exc)
        return None


class CostTracker:
    """Thread-safe accumulator of :class:`UsageEvent` records for one session.

    Usage::

        tracker = CostTracker()
        tracker.record(UsageEvent(turn=1, model="gpt-4o", provider="openai",
                                  prompt_tokens=1000, completion_tokens=200))
        cost = tracker.session_cost()
        print(cost.summary_str())
    """

    def __init__(self) -> None:
        self._events: list[UsageEvent] = []
        self._lock = threading.Lock()

    def record(self, event: UsageEvent) -> None:
        """Append a :class:`UsageEvent` to the session.

        Thread-safe.

        Args:
            event: Usage event from an LLM call.
        """
        with self._lock:
            self._events.append(event)
            logger.debug(
                "CostTracker: turn=%d model=%s in=%d out=%d cost=$%.5f",
                event.turn,
                event.model,
                event.prompt_tokens,
                event.completion_tokens,
                event.cost_usd(),
            )
        # Defence in depth: record_last_turn() already swallows its own failures,
        # but this call sits on the agentic hot path, so the *call* must be safe
        # too — a replaced sink, an import-time failure or a future refactor must
        # not be able to turn a telemetry note into a dead turn.
        try:
            record_last_turn(event)
        except Exception as exc:  # noqa: BLE001
            logger.debug("last-turn snapshot skipped: %s", exc)

    def session_cost(self) -> SessionCost:
        """Return accumulated cost and token statistics for the session.

        Returns:
            :class:`SessionCost` snapshot (safe to call at any time).
        """
        with self._lock:
            events = list(self._events)

        total_usd = sum(ev.cost_usd() for ev in events)
        total_tokens = sum(ev.total_tokens for ev in events)
        return SessionCost(
            total_usd=total_usd,
            total_tokens=total_tokens,
            events=events,
        )

    def reset(self) -> None:
        """Clear all recorded events (start a new session)."""
        with self._lock:
            self._events.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)


# ─────────────────────────────────────────────────────────────
# IterationBudget (F-01 dependency)
# ─────────────────────────────────────────────────────────────


class IterationBudget:
    """Thread-safe shared iteration counter for parent + child agents.

    The budget tracks how many LLM-call iterations have been consumed
    across the entire agent tree (parent + delegated sub-agents).

    Usage::

        budget = IterationBudget(max_iterations=90)
        budget.consume(1)                    # decrement by 1
        budget.budget_used_pct()            # → 0.011 (1.1%)
        budget.is_exhausted()               # → False
        child_budget = budget.child(max_iterations=30)  # child shares same counter

    """

    def __init__(self, max_iterations: int = 90) -> None:
        self._max = max_iterations
        self._used: int = 0
        self._lock = threading.Lock()

    def consume(self, n: int = 1) -> None:
        """Consume *n* iterations.  No-op if budget already exhausted."""
        with self._lock:
            self._used = min(self._used + n, self._max)

    def budget_used_pct(self) -> float:
        """Return fraction of budget consumed (0.0–1.0)."""
        with self._lock:
            if self._max == 0:
                return 1.0
            return self._used / self._max

    def remaining(self) -> int:
        """Return number of iterations remaining."""
        with self._lock:
            return max(0, self._max - self._used)

    def is_exhausted(self) -> bool:
        """Return True if no iterations remain."""
        return self.remaining() == 0

    def child(self, max_iterations: int | None = None) -> IterationBudget:
        """Create a child budget that shares the same counter.

        The child is capped at *max_iterations* (default: ``min(remaining * 0.5, 30)``).
        """
        available = self.remaining()
        if max_iterations is None:
            max_iterations = min(int(available * 0.5), 30)
        max_iterations = min(max_iterations, available)
        return _SharedIterationBudget(parent=self, max_iterations=max_iterations)

    @property
    def max_iterations(self) -> int:
        return self._max


class _SharedIterationBudget(IterationBudget):
    """Child budget that consumes from the parent's shared counter."""

    def __init__(self, parent: IterationBudget, max_iterations: int) -> None:
        super().__init__(max_iterations=max_iterations)
        self._parent = parent

    def consume(self, n: int = 1) -> None:
        super().consume(n)
        self._parent.consume(n)  # also deduct from parent

    def remaining(self) -> int:
        """Cap at parent's remaining budget."""
        own_remaining = super().remaining()
        parent_remaining = self._parent.remaining()
        return min(own_remaining, parent_remaining)
