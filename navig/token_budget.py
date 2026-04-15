"""
Token Budget — diminishing-returns continuation guard.

Ported from .lab/claude/query/ (MIT, Anthropic).  Pure logic; no I/O.

The check prevents unbounded LLM continuation loops: if the model has
continued at least ``min_continuation_count`` times AND each of the last
``consecutive_low_delta`` turns added fewer than ``min_delta_tokens`` new
tokens, the continuation should stop.

Usage::

    from navig.token_budget import create_budget_tracker, update_tracker, check_budget

    tracker = create_budget_tracker()
    for turn in continuation_turns:
        response = call_llm(...)
        tracker = update_tracker(tracker, total_tokens_so_far)
        decision = check_budget(tracker, cfg)
        if decision.action == "stop":
            logger.info("Stopping: %s", decision.reason)
            break

All tunables come from ``config/defaults.yaml`` under ``token_budget:``.
No numeric literals are used inside the decision logic.
"""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Literal

logger = logging.getLogger("navig.token_budget")

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class BudgetTracker:
    """Immutable-style token-budget state passed between turns."""

    continuation_count: int = 0
    last_total_tokens: int = 0
    delta_history: list[int] = dataclasses.field(default_factory=list)
    started_at: float = dataclasses.field(default_factory=time.monotonic)


@dataclasses.dataclass
class ContinueDecision:
    action: Literal["continue"] = "continue"
    nudge_message: str = ""


@dataclasses.dataclass
class StopDecision:
    action: Literal["stop"] = "stop"
    reason: str = ""
    completion_event: str = "token_budget_stop"


BudgetDecision = ContinueDecision | StopDecision


# ---------------------------------------------------------------------------
# Public API — pure functions
# ---------------------------------------------------------------------------


def create_budget_tracker() -> BudgetTracker:
    """Create a fresh tracker for a new request."""
    return BudgetTracker()


def update_tracker(tracker: BudgetTracker, new_total_tokens: int) -> BudgetTracker:
    """
    Return a new tracker reflecting *new_total_tokens* from the latest turn.

    Args:
        tracker:          Current tracker state.
        new_total_tokens: Cumulative tokens after the latest response.

    Returns:
        Updated ``BudgetTracker`` (previous instance is unmodified).
    """
    delta = max(0, new_total_tokens - tracker.last_total_tokens)
    return BudgetTracker(
        continuation_count=tracker.continuation_count + 1,
        last_total_tokens=new_total_tokens,
        delta_history=tracker.delta_history + [delta],
        started_at=tracker.started_at,
    )


def check_budget(
    tracker: BudgetTracker,
    cfg: dict[str, Any] | None = None,
) -> BudgetDecision:
    """
    Decide whether the LLM should continue or stop based on token budget.

    Decision logic (all values from *cfg*, never hardcoded):
    1. Hard cap: if ``continuation_count >= max_continuations`` → stop.
    2. Diminishing-returns: if ``continuation_count >= min_continuation_count``
       AND the last ``consecutive_low_delta`` deltas are all below
       ``min_delta_tokens`` → stop.
    3. Otherwise → continue, with an optional nudge when approaching the cap.

    Args:
        tracker: Current budget state.
        cfg:     ``token_budget:`` config dict (from ``config/defaults.yaml``).
                 If *None*, values are loaded from ConfigManager on each call.

    Returns:
        ``ContinueDecision`` or ``StopDecision``.
    """
    if cfg is None:
        cfg = _load_budget_config()

    max_cont = int(cfg.get("max_continuations", 8))
    min_cont = int(cfg.get("min_continuation_count", 3))
    min_delta = int(cfg.get("min_delta_tokens", 500))
    consec = int(cfg.get("consecutive_low_delta", 2))

    count = tracker.continuation_count

    # Rule 1 — hard cap
    if count >= max_cont:
        logger.debug(
            "token_budget: hard cap reached (%d >= %d)", count, max_cont
        )
        return StopDecision(
            reason=f"Hard continuation cap ({max_cont}) reached.",
            completion_event="token_budget_hard_cap",
        )

    # Rule 2 — diminishing returns
    if count >= min_cont and len(tracker.delta_history) >= consec:
        recent = tracker.delta_history[-consec:]
        if all(d < min_delta for d in recent):
            logger.debug(
                "token_budget: diminishing returns — last %d deltas %s < %d",
                consec,
                recent,
                min_delta,
            )
            return StopDecision(
                reason=(
                    f"Diminishing returns after {count} turns: "
                    f"last {consec} deltas {recent} each < {min_delta} tokens."
                ),
                completion_event="token_budget_diminishing",
            )

    # Rule 3 — continue, nudge if getting close to cap
    nudge = ""
    if count >= max_cont - 2:
        nudge = (
            f"[dim]Token budget: {count + 1}/{max_cont} continuations used.[/dim]"
        )

    return ContinueDecision(nudge_message=nudge)


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------


def _load_budget_config() -> dict[str, Any]:
    """Load ``token_budget:`` section from config.yaml with graceful fallback."""
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        raw = cm.get("token_budget")
        if isinstance(raw, dict):
            return raw
    except Exception as exc:  # noqa: BLE001
        logger.debug("token_budget config load failed: %s", exc)
    return {}
