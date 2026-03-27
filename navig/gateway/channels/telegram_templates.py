"""
Telegram Message Templates — T1–T10

Enforces spec character limits and provides structured formatters for
each template type. Templates respect the user's verbosity preference.

Verbosity levels:
  brief   — shortest form, base template only
  normal  — base template (default)
  detailed — verbose variant with extra context
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# Character limits (from spec)
# ────────────────────────────────────────────────────────────────

DEFAULT_LIMIT = 280  # Standard reply
STATUS_LIMIT = 200  # Status / completion
GREETING_LIMIT = 120  # T1 base
ACK_LIMIT = 80  # T7 acknowledgment
TASK_DONE_LIMIT = 100  # T4 base
BRIEFING_LIMIT = 300  # T5 base
MAX_SINGLE_MSG = 2000  # Split above this
SPLIT_THRESHOLD = 500  # Soft split target for multi-part


class TemplateID(str, Enum):
    """Template identifiers matching the spec."""

    GREETING = "T1"
    STATUS = "T2"
    INCIDENT = "T3"
    TASK_DONE = "T4"
    BRIEFING = "T5"
    CLARIFICATION = "T6"
    ACK = "T7"
    APPROVAL = "T8"
    THINKING = "T9"
    ERROR = "T10"


@dataclass
class FormattedMessage:
    """A formatted message ready for Telegram."""

    text: str
    template_id: Optional[TemplateID] = None
    keyboard_profile: Optional[str] = None  # "action", "expand", "feedback", "none"
    parts: Optional[List[str]] = None  # Multi-part if split


# ────────────────────────────────────────────────────────────────
# Template formatters
# ────────────────────────────────────────────────────────────────


def t1_greeting(
    username: str = "",
    time_greeting: str = "…present",
    top_priority: str = "",
    overnight_summary: str = "",
    verbosity: str = "normal",
) -> FormattedMessage:
    """T1 — Greeting (session start) — entity surfacing."""
    base = f"{time_greeting}. systems stable. what are we working on?"
    if verbosity == "detailed" and (top_priority or overnight_summary):
        parts = [base]
        if top_priority:
            parts.append(f"top thread: {top_priority}.")
        if overnight_summary:
            parts.append(f"overnight: {overnight_summary}.")
        text = " ".join(parts)
        text = _enforce_limit(text, 200)
    else:
        text = _enforce_limit(base, GREETING_LIMIT)
    return FormattedMessage(
        text=text, template_id=TemplateID.GREETING, keyboard_profile="none"
    )


def t2_status(
    summary: str,
    tasks_done: int = 0,
    tasks_pending: int = 0,
    next_action: str = "",
    items: Optional[List[str]] = None,
    verbosity: str = "normal",
) -> FormattedMessage:
    """T2 — Status report — entity awareness."""
    health = "✅" if tasks_pending == 0 else "⚠️"
    base = f"{health} all threads nominal. {tasks_done} closed, {tasks_pending} open."
    if next_action:
        base += f" next: {next_action}."
    base = _enforce_limit(base, STATUS_LIMIT)

    if verbosity == "detailed" and items:
        bullets = "\n".join(f"• {item}" for item in items[:5])
        text = f"{base}\n\n{bullets}"
        if len(items) > 5:
            text += "\n\n_See all in Deck_"
            return FormattedMessage(
                text=text, template_id=TemplateID.STATUS, keyboard_profile="expand"
            )
        return FormattedMessage(
            text=text, template_id=TemplateID.STATUS, keyboard_profile="none"
        )
    return FormattedMessage(
        text=base, template_id=TemplateID.STATUS, keyboard_profile="none"
    )


def t3_incident(
    service: str,
    impact: str,
    action_taken: str,
    human_needed: bool = False,
    verbosity: str = "normal",
) -> FormattedMessage:
    """T3 — Incident / alert — entity alarm."""
    human_clause = "need your call on this." if human_needed else "handled it."
    base = (
        f"🚨 {service} went down. impact: {impact}. I've {action_taken}. {human_clause}"
    )
    base = _enforce_limit(base, 200)
    return FormattedMessage(
        text=base, template_id=TemplateID.INCIDENT, keyboard_profile="action"
    )


def t4_task_done(
    task_name: str,
    result: str = "",
    method_summary: str = "",
    verbosity: str = "normal",
) -> FormattedMessage:
    """T4 — Task completion — entity confirmation."""
    base = f"✅ {task_name} — done."
    if result:
        base += f" {result}."
    base = _enforce_limit(base, TASK_DONE_LIMIT)

    if verbosity == "detailed" and method_summary:
        text = f"{base}\n{method_summary}"
        text = _enforce_limit(text, 200)
        return FormattedMessage(
            text=text, template_id=TemplateID.TASK_DONE, keyboard_profile="none"
        )
    return FormattedMessage(
        text=base, template_id=TemplateID.TASK_DONE, keyboard_profile="none"
    )


def t5_briefing(
    items: List[str],
    verbosity: str = "normal",
) -> FormattedMessage:
    """T5 — Summary / briefing."""
    header = "📊 *Daily Briefing*\n"
    bullets = "\n".join(f"• {item}" for item in items[:5])
    text = f"{header}\n{bullets}"
    if len(items) > 5:
        text += "\n\n_Open briefing in Deck for full view._"
        return FormattedMessage(
            text=_enforce_limit(text, BRIEFING_LIMIT + 100),
            template_id=TemplateID.BRIEFING,
            keyboard_profile="expand",
        )
    return FormattedMessage(
        text=_enforce_limit(text, BRIEFING_LIMIT),
        template_id=TemplateID.BRIEFING,
        keyboard_profile="none",
    )


def t6_clarification(
    what_needed: str,
    question: str,
    options: Optional[List[str]] = None,
    verbosity: str = "normal",
) -> FormattedMessage:
    """T6 — Clarification request — entity asking."""
    base = f"…missing context: {what_needed}. {question}?"
    base = _enforce_limit(base, 150)
    if options and len(options) <= 3:
        # Options as inline buttons are handled by keyboard system
        return FormattedMessage(
            text=base, template_id=TemplateID.CLARIFICATION, keyboard_profile="action"
        )
    return FormattedMessage(
        text=base, template_id=TemplateID.CLARIFICATION, keyboard_profile="none"
    )


def t7_ack(
    next_suggestion: str = "",
    verbosity: str = "normal",
) -> FormattedMessage:
    """T7 — Acknowledgment — entity nod."""
    base = "noted."
    if next_suggestion:
        base += f" {next_suggestion}"
    base = _enforce_limit(base, ACK_LIMIT)
    return FormattedMessage(
        text=base, template_id=TemplateID.ACK, keyboard_profile="none"
    )


def t8_approval(
    action: str,
    risk: str,
    alternative: str,
    verbosity: str = "normal",
) -> FormattedMessage:
    """T8 — Approval request — entity requesting consent."""
    base = f"⚠️ I want to {action}. risk: {risk}. alternative: {alternative}."
    base = _enforce_limit(base, 250)
    return FormattedMessage(
        text=base, template_id=TemplateID.APPROVAL, keyboard_profile="action"
    )


def t10_error(
    what_failed: str,
    why: str,
    what_next: str,
    verbosity: str = "normal",
) -> FormattedMessage:
    """T10 — Error / failure — entity stumble."""
    base = f"❌ …{what_failed}. {why}. {what_next}."
    base = _enforce_limit(base, 200)
    return FormattedMessage(
        text=base, template_id=TemplateID.ERROR, keyboard_profile="feedback"
    )


# ────────────────────────────────────────────────────────────────
# Response post-processing
# ────────────────────────────────────────────────────────────────


def enforce_response_limits(
    text: str,
    verbosity: str = "normal",
    max_single: int = MAX_SINGLE_MSG,
    split_at: int = SPLIT_THRESHOLD,
) -> FormattedMessage:
    """
    Post-process any AI response to obey char limits.

    - brief: truncate to DEFAULT_LIMIT
    - normal: keep up to max_single, split if over
    - detailed: keep full, split at split_at boundaries
    """
    if verbosity == "brief":
        text = _enforce_limit(text, DEFAULT_LIMIT)
        return FormattedMessage(text=text)

    if len(text) <= max_single:
        return FormattedMessage(text=text)

    # Split into parts at paragraph boundaries
    parts = _smart_split(text, split_at)
    return FormattedMessage(text=parts[0], parts=parts)


def _enforce_limit(text: str, limit: int) -> str:
    """Truncate text to limit, appending ellipsis if needed."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _smart_split(text: str, target_size: int) -> List[str]:
    """Split text into chunks at paragraph/sentence boundaries."""
    parts: List[str] = []
    remaining = text

    while len(remaining) > target_size:
        # Try to split at double newline (paragraph)
        idx = remaining.rfind("\n\n", 0, target_size)
        if idx == -1:
            # Try single newline
            idx = remaining.rfind("\n", 0, target_size)
        if idx == -1:
            # Try sentence boundary
            idx = remaining.rfind(". ", 0, target_size)
            if idx != -1:
                idx += 1  # include the period
        if idx == -1:
            # Hard split at target
            idx = target_size

        parts.append(remaining[:idx].strip())
        remaining = remaining[idx:].strip()

    if remaining:
        parts.append(remaining)

    return parts


# ────────────────────────────────────────────────────────────────
# Auto-detect template from AI response content
# ────────────────────────────────────────────────────────────────

_GREETING_RE = re.compile(
    r"^(hey|hi|hello|good\s+(morning|afternoon|evening)|"
    r"welcome|glad|nice|thanks|thank\s+you)",
    re.IGNORECASE,
)
_STATUS_RE = re.compile(r"(all\s+systems|status|health|uptime|running)", re.IGNORECASE)
_ERROR_RE = re.compile(r"^(❌|error|failed|sorry,?\s+i\s+can)", re.IGNORECASE)
_ACK_RE = re.compile(
    r"^(glad|no\s+problem|you'?re\s+welcome|done|got\s+it)", re.IGNORECASE
)


def auto_detect_template(ai_response: str) -> Optional[TemplateID]:
    """
    Heuristic: detect which template an AI response most closely matches.
    Returns None if no strong match (generic response).
    """
    first_line = ai_response.strip().split("\n")[0]
    if _ACK_RE.search(first_line) and len(ai_response) < ACK_LIMIT + 20:
        return TemplateID.ACK
    if _GREETING_RE.search(first_line) and len(ai_response) < GREETING_LIMIT + 40:
        return TemplateID.GREETING
    if _ERROR_RE.search(first_line):
        return TemplateID.ERROR
    if ai_response.startswith("🚨"):
        return TemplateID.INCIDENT
    if ai_response.startswith("📊"):
        return TemplateID.BRIEFING
    return None
