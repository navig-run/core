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

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Pricing table  (USD per 1,000,000 tokens)
# Format: {model_name: (input_per_M, output_per_M, cache_read_per_M, cache_write_per_M)}
# ─────────────────────────────────────────────────────────────

PRICE_TABLE: dict[str, tuple[float, float, float, float]] = {
    # OpenAI
    "gpt-4o":                    (2.50,  10.00, 1.25,  0.00),
    "gpt-4o-mini":               (0.15,   0.60, 0.075, 0.00),
    "gpt-4-turbo":               (10.00,  30.00, 0.00, 0.00),
    "gpt-4":                     (30.00,  60.00, 0.00, 0.00),
    "gpt-3.5-turbo":             (0.50,   1.50,  0.00, 0.00),
    "o1":                        (15.00,  60.00, 0.00, 0.00),
    "o1-mini":                   (3.00,   12.00, 0.00, 0.00),
    "o3":                        (10.00,  40.00, 0.00, 0.00),
    "o3-mini":                   (1.10,   4.40,  0.00, 0.00),
    "o4-mini":                   (1.10,   4.40,  0.00, 0.00),
    # Anthropic Claude
    "claude-opus-4-5":           (15.00,  75.00, 1.50, 18.75),
    "claude-opus-4":             (15.00,  75.00, 1.50, 18.75),
    "claude-sonnet-4-5":         (3.00,   15.00, 0.30,  3.75),
    "claude-sonnet-4":           (3.00,   15.00, 0.30,  3.75),
    "claude-3-5-sonnet-20241022":(3.00,   15.00, 0.30,  3.75),
    "claude-3-5-haiku-20241022": (0.80,   4.00,  0.08,  1.00),
    "claude-3-opus-20240229":    (15.00,  75.00, 1.50, 18.75),
    "claude-3-haiku-20240307":   (0.25,   1.25,  0.03,  0.30),
    # Google Gemini
    "gemini-2.5-pro":            (1.25,   5.00,  0.00,  0.00),
    "gemini-2.5-flash":          (0.075,  0.30,  0.00,  0.00),
    "gemini-1.5-pro":            (1.25,   5.00,  0.00,  0.00),
    "gemini-1.5-flash":          (0.075,  0.30,  0.00,  0.00),
    # Nous Research (via OpenRouter)
    "hermes-3-70b":              (0.70,   0.80,  0.00,  0.00),
    "hermes-3-405b":             (1.79,   1.79,  0.00,  0.00),
    # Mistral
    "mistral-large-latest":      (3.00,   9.00,  0.00,  0.00),
    "mistral-small-latest":      (1.00,   3.00,  0.00,  0.00),
}


def _lookup_price(model: str) -> tuple[float, float, float, float]:
    """Return (input_per_M, output_per_M, cache_read_per_M, cache_write_per_M) for *model*.

    Tries exact match first, then prefix match.  Returns zeros for unknown models.
    """
    if model in PRICE_TABLE:
        return PRICE_TABLE[model]

    # Prefix match (handles versioned names like "claude-3-5-sonnet-20241022" → "claude-3-5-sonnet")
    for key, prices in PRICE_TABLE.items():
        if model.startswith(key) or key.startswith(model):
            return prices

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
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Sum of prompt + completion tokens."""
        return self.prompt_tokens + self.completion_tokens

    def cost_usd(self) -> float:
        """Estimated USD cost for this event."""
        inp, out, cache_r, cache_w = _lookup_price(self.model)
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
