"""navig.agent.proactive.starters — chat starter suggestions built from the user's sources.

The empty chat screen used to rotate fixed hints. These come from what the user actually
has: memories waiting for review and the accounts they connected. Pure reads, no
network and no side effects (unlike ``EngagementCoordinator.engagement_tick``), so the
deck route can serve them instantly:

``[{"id", "label", "prompt", "source", "action"?}]`` — ``prompt`` fills the composer;
``action`` (when present) is a navigation the UI performs instead, e.g.
``"settings:personalization"``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_STARTERS = 4

#: connector id → (label, prompt). Phrased as the user would ask; search_all and the
#: connector tools answer them. Order = priority when more than MAX_STARTERS apply.
CONNECTOR_STARTERS: dict[str, tuple[str, str]] = {
    "gmail": ("Catch up on email", "Summarize my unread email and flag anything urgent."),
    "google_calendar": ("Today’s calendar", "What’s on my calendar today, and what should I prepare?"),
    "github": ("GitHub to-dos", "What needs my attention on GitHub right now?"),
    "slack": ("Catch up on Slack", "Catch me up on Slack — what did I miss?"),
    "linear": ("My Linear issues", "Which Linear issues are assigned to me, most urgent first?"),
    "notion": ("Recent notes", "Find my most recent notes in Notion and summarize them."),
    "google_drive": ("Find a document", "Find the latest documents in my Drive and tell me what changed."),
    "youtube": ("My channel", "How is my YouTube channel doing this week?"),
}


def _pending_memory_count() -> int:
    try:
        from navig.memory.controls import memory_enabled
        from navig.memory.key_facts import get_key_fact_store

        if not memory_enabled():
            return 0
        return len(get_key_fact_store().get_pending(limit=100))
    except Exception as exc:  # noqa: BLE001 — a starter must never break the chat screen
        logger.debug("pending-memory count unavailable: %s", exc)
        return 0


def _connected_ids() -> list[str]:
    try:
        from navig.connectors.connected import vault_connected_ids

        return list(vault_connected_ids())
    except Exception as exc:  # noqa: BLE001
        logger.debug("connected connectors unavailable: %s", exc)
        return []


def build_starters() -> list[dict[str, Any]]:
    """Up to MAX_STARTERS suggestions; an empty list means "use the static hints"."""
    out: list[dict[str, Any]] = []
    pending = _pending_memory_count()
    if pending:
        out.append(
            {
                "id": "memory-review",
                "label": f"Review {pending} thing{'s' if pending != 1 else ''} NAVIG learned",
                "prompt": "",
                "source": "memory",
                "action": "settings:personalization",
                "count": pending,
            }
        )
    connected = set(_connected_ids())
    for cid, (label, prompt) in CONNECTOR_STARTERS.items():
        if len(out) >= MAX_STARTERS:
            break
        if cid in connected:
            out.append({"id": f"connector-{cid}", "label": label, "prompt": prompt, "source": cid})
    return out
