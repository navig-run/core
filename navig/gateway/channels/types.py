"""Shared type definitions for NAVIG Telegram channel adapters.

Import here to avoid circular dependencies between telegram.py, telegram_keyboards.py,
and telegram_sessions.py.
"""
from __future__ import annotations

from typing import TypedDict


class ContextMessage(TypedDict):
    """A single message entry from session history passed to the LLM."""

    role: str        # "user" | "assistant"
    content: str


class MessageMetadata(TypedDict, total=False):
    """Typed metadata dict threaded through every message-handling pipeline stage.

    ``total=False`` means every key is optional — the dict starts empty and is
    populated progressively as a message moves through the routing pipeline.
    Fields that are always present for normal text messages are documented as
    **always set**; optional fields are set only when the relevant feature is
    active.
    """

    # ── Identity (always set for any authorized message) ──────────────────
    chat_id: int           # Telegram chat identifier
    user_id: int           # Telegram user identifier
    username: str          # Telegram @handle (fallback: str(user_id))
    message_id: int        # Originating message_id
    is_group: bool         # True for group/supergroup chats
    reply_to: int | None  # message_id being replied to, if any

    # ── Session context (set when HAS_SESSIONS=True) ───────────────────────
    session_key: str                              # Composite session key
    context_messages: list[ContextMessage]        # Recent turns for LLM context

    # ── Routing hints ──────────────────────────────────────────────────────
    tier_override: str       # "big" | "small" | "coder_big" | "" (persistent or per-msg)
    detected_language: str   # BCP-47 code detected by STT, e.g. "fr", "de" (voice only)
    resolved_model: str      # provider:model selected by upstream router (optional)
