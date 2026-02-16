"""
Telegram Inline Keyboard System for NAVIG Gateway — v2

Three keyboard profiles (max 2 rows, max 3 buttons/row):
  action   — NAVIG proposes something: [Approve] [Reject] [Details]
  expand   — Response was truncated:   [Show more] [Open in Deck]
  feedback — Normal reply:             [👍] [👎]
  none     — Greetings, acks, short conversational (no keyboard)

Callback data schema:  action:hash
  action = button action type
  hash   = 6-char MD5 key into CallbackStore
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from navig.gateway.channels.telegram import TelegramChannel

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────

MAX_BUTTONS_PER_ROW = 3
MAX_ROWS = 2
MAX_BUTTON_TEXT = 28
MAX_CALLBACK_DATA = 64  # Telegram limit


class ContentCategory(str, Enum):
    """Categories used to decide which keyboard profile to use."""
    INFORMATIONAL = "info"
    COMPARISON = "compare"
    HOWTO = "howto"
    CODE = "code"
    OPINION = "opinion"
    LIST = "list"
    CONVERSATIONAL = "chat"
    ERROR = "error"


class KeyboardProfile(str, Enum):
    """Keyboard profile — determines which rows to show."""
    ACTION = "action"       # Approve/Reject/Details
    EXPAND = "expand"       # Show more / Open in Deck
    FEEDBACK = "feedback"   # 👍 👎
    NONE = "none"           # No keyboard


# ────────────────────────────────────────────────────────────────
# Content classifier (fast regex — no LLM call)
# ────────────────────────────────────────────────────────────────

_CODE_BLOCK = re.compile(r"```[\s\S]{10,}?```")
_NUMBERED_LIST = re.compile(r"(?m)^\s*\d+[\.\)]\s+.+", re.MULTILINE)
_BULLET_LIST = re.compile(r"(?m)^\s*[-*•]\s+.+", re.MULTILINE)
_COMPARISON_WORDS = re.compile(
    r"\b(pros?\b.*cons?|advantages?.*disadvantages?|"
    r"comparison|vs\.?|versus|differ|better|worse|"
    r"alternative|option\s+[a-d12-4])\b",
    re.IGNORECASE,
)
_HOWTO_SIGNALS = re.compile(
    r"\b(step\s+\d|first[\s,].*then|how\s+to|tutorial|"
    r"guide|instructions?|follow\s+these|here'?s?\s+how)\b",
    re.IGNORECASE,
)
_OPINION_SIGNALS = re.compile(
    r"\b(recommend|suggest|my\s+opinion|I\s+think|"
    r"personally|better\s+choice|prefer)\b",
    re.IGNORECASE,
)
_ERROR_SIGNALS = re.compile(
    r"\b(sorry|cannot|unable|don'?t\s+(know|understand)|"
    r"no\s+information|error|failed)\b",
    re.IGNORECASE,
)
_GREETING_SIGNALS = re.compile(
    r"^\s*(hey|hi|hello|good\s+(morning|afternoon|evening)|"
    r"glad|welcome|nice|thanks|thank\s+you|no\s+problem)",
    re.IGNORECASE,
)
_SHORT_ACK_MAX = 120  # responses shorter than this are likely acks/greetings


def classify_response(text: str) -> ContentCategory:
    """Classify an AI response into a content category (no LLM call)."""
    if _CODE_BLOCK.search(text):
        return ContentCategory.CODE
    if _COMPARISON_WORDS.search(text):
        return ContentCategory.COMPARISON
    if _HOWTO_SIGNALS.search(text):
        return ContentCategory.HOWTO
    if _ERROR_SIGNALS.search(text) and len(text) < 300:
        return ContentCategory.ERROR
    if _OPINION_SIGNALS.search(text):
        return ContentCategory.OPINION

    numbered = len(_NUMBERED_LIST.findall(text))
    bulleted = len(_BULLET_LIST.findall(text))
    if numbered >= 3 or bulleted >= 4:
        return ContentCategory.LIST

    if len(text) > 600:
        return ContentCategory.INFORMATIONAL

    return ContentCategory.CONVERSATIONAL


def choose_profile(
    ai_response: str,
    category: ContentCategory,
    *,
    has_approval: bool = False,
) -> KeyboardProfile:
    """
    Choose which keyboard profile to apply.

    Rules:
      1. If the response contains an approval request → ACTION
      2. If the response is short conversational / greeting / ack → NONE
      3. If the response is long (>500 chars) or has code → EXPAND
      4. Otherwise → FEEDBACK (subtle thumbs)
    """
    if has_approval:
        return KeyboardProfile.ACTION

    # Short greetings, acks, errors — no buttons
    if len(ai_response) < _SHORT_ACK_MAX:
        return KeyboardProfile.NONE
    if category == ContentCategory.CONVERSATIONAL and len(ai_response) < 250:
        return KeyboardProfile.NONE
    if _GREETING_SIGNALS.match(ai_response):
        return KeyboardProfile.NONE
    if category == ContentCategory.ERROR and len(ai_response) < 300:
        return KeyboardProfile.NONE

    # Long responses or code → offer expand
    if len(ai_response) > 500 or category == ContentCategory.CODE:
        return KeyboardProfile.EXPAND

    # Default → no buttons. Bare feedback (👍👎) is visual noise
    # that adds no value without aggregation backend.
    return KeyboardProfile.NONE


# ────────────────────────────────────────────────────────────────
# Callback data store (bounded in-memory)
# ────────────────────────────────────────────────────────────────

def _short_hash(text: str, length: int = 6) -> str:
    return hashlib.md5(text.encode()).hexdigest()[:length]


@dataclass
class CallbackEntry:
    """Stored context for a callback button."""
    action: str
    user_message: str
    ai_response: str
    category: str
    extra: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class CallbackStore:
    """
    Bounded in-memory store: callback_data key → full context.
    Telegram limits callback_data to 64 bytes, so we store context here.
    """

    def __init__(self, max_entries: int = 500):
        self._store: Dict[str, CallbackEntry] = {}
        self._max = max_entries

    def put(self, key: str, entry: CallbackEntry) -> None:
        if len(self._store) >= self._max:
            items = sorted(self._store.items(), key=lambda kv: kv[1].created_at)
            to_remove = len(items) // 5 or 1
            for k, _ in items[:to_remove]:
                del self._store[k]
        self._store[key] = entry

    def get(self, key: str) -> Optional[CallbackEntry]:
        return self._store.get(key)

    def remove(self, key: str) -> None:
        self._store.pop(key, None)


_callback_store = CallbackStore()


def get_callback_store() -> CallbackStore:
    return _callback_store


# ────────────────────────────────────────────────────────────────
# ResponseKeyboardBuilder
# ────────────────────────────────────────────────────────────────

class ResponseKeyboardBuilder:
    """
    Builds an InlineKeyboardMarkup (list-of-lists-of-dicts) using one of
    three profiles: action, expand, feedback — or returns None.

    Max 2 rows × 3 buttons. Buttons shown only when they save typing.
    """

    def __init__(self, store: Optional[CallbackStore] = None):
        self.store = store or get_callback_store()

    def build(
        self,
        ai_response: str,
        user_message: str = "",
        message_id: int = 0,
        *,
        profile_override: Optional[str] = None,
        approval_actions: Optional[List[Dict[str, str]]] = None,
    ) -> Optional[List[List[Dict[str, str]]]]:
        """
        Build inline keyboard for an AI response.

        Args:
            ai_response: The AI-generated text.
            user_message: The original user message.
            message_id: Telegram message ID.
            profile_override: Force a profile ("action"/"expand"/"feedback"/"none").
            approval_actions: For action profile — list of {"label": ..., "action": ...}.

        Returns:
            List of button rows, or None.
        """
        category = classify_response(ai_response)
        has_approval = bool(approval_actions)

        if profile_override:
            try:
                profile = KeyboardProfile(profile_override)
            except ValueError:
                profile = choose_profile(ai_response, category, has_approval=has_approval)
        else:
            profile = choose_profile(ai_response, category, has_approval=has_approval)

        if profile == KeyboardProfile.NONE:
            return None

        msg_hash = _short_hash(f"{user_message}:{message_id}")
        rows: List[List[Dict[str, str]]] = []

        if profile == KeyboardProfile.ACTION:
            rows = self._build_action_rows(
                msg_hash, user_message, ai_response, category, approval_actions
            )
        elif profile == KeyboardProfile.EXPAND:
            rows = self._build_expand_rows(msg_hash, user_message, ai_response, category)
        elif profile == KeyboardProfile.FEEDBACK:
            rows = self._build_feedback_rows(msg_hash, user_message, ai_response)

        return rows[:MAX_ROWS] if rows else None

    # ── Profile builders ──

    def _make_button(
        self,
        text: str,
        action: str,
        msg_hash: str,
        user_message: str = "",
        ai_response: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        label = text[:MAX_BUTTON_TEXT]
        cb_key = f"{action}:{msg_hash}"
        if len(cb_key) > MAX_CALLBACK_DATA:
            cb_key = cb_key[:MAX_CALLBACK_DATA]

        self.store.put(cb_key, CallbackEntry(
            action=action,
            user_message=user_message,
            ai_response=ai_response[:3000],
            category=classify_response(ai_response).value,
            extra=extra or {},
        ))
        return {"text": label, "callback_data": cb_key}

    def _build_action_rows(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
        category: ContentCategory,
        approval_actions: Optional[List[Dict[str, str]]] = None,
    ) -> List[List[Dict[str, str]]]:
        """Action profile: [Approve] [Alternative] [Cancel] or custom."""
        if approval_actions:
            row = []
            for item in approval_actions[:MAX_BUTTONS_PER_ROW]:
                row.append(self._make_button(
                    item["label"], item["action"], msg_hash, user_message, ai_response
                ))
            return [row]

        # Default approval pattern
        return [[
            self._make_button("✅ Approve", "approve", msg_hash, user_message, ai_response),
            self._make_button("🔀 Alternative", "alternative", msg_hash, user_message, ai_response),
            self._make_button("❌ Cancel", "cancel", msg_hash, user_message, ai_response),
        ]]

    def _build_expand_rows(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
        category: ContentCategory,
    ) -> List[List[Dict[str, str]]]:
        """
        Expand profile: context-aware row 1 + optional feedback row 2.

        Row 1 adapts to content:
          CODE  → [Explain] [Copy code]
          HOWTO → [Summarize] [Show steps]
          Other → [Summarize] [Go deeper]
        Row 2 → [👍] [👎] (always)
        """
        # Row 1 — contextual actions
        if category == ContentCategory.CODE:
            row1 = [
                self._make_button("🔍 Explain", "explain", msg_hash, user_message, ai_response),
                self._make_button("📋 Copy code", "copy_code", msg_hash, user_message, ai_response),
            ]
        elif category == ContentCategory.HOWTO:
            row1 = [
                self._make_button("📋 Summarize", "summarize", msg_hash, user_message, ai_response),
                self._make_button("📝 Show steps", "show_steps", msg_hash, user_message, ai_response),
            ]
        elif category == ContentCategory.COMPARISON:
            row1 = [
                self._make_button("📊 Compare", "table_fmt", msg_hash, user_message, ai_response),
                self._make_button("✅ Recommend", "recommend", msg_hash, user_message, ai_response),
            ]
        else:
            row1 = [
                self._make_button("📋 Summarize", "summarize", msg_hash, user_message, ai_response),
                self._make_button("🔍 Go deeper", "elaborate", msg_hash, user_message, ai_response),
            ]

        # Row 2 — feedback
        row2 = [
            self._make_button("👍", "fb_up", msg_hash, user_message, ai_response),
            self._make_button("👎", "fb_down", msg_hash, user_message, ai_response),
        ]

        return [row1, row2]

    def _build_feedback_rows(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
    ) -> List[List[Dict[str, str]]]:
        """Feedback profile: just [👍] [👎]."""
        return [[
            self._make_button("👍", "fb_up", msg_hash, user_message, ai_response),
            self._make_button("👎", "fb_down", msg_hash, user_message, ai_response),
        ]]


# ────────────────────────────────────────────────────────────────
# CallbackHandler
# ────────────────────────────────────────────────────────────────

_ACTION_PROMPTS: Dict[str, str] = {
    "regen": (
        "The user asked: \"{user_message}\"\n"
        "Your previous answer was not satisfactory. "
        "Provide a better, more complete answer."
    ),
    "summarize": (
        "Summarize the following in 2-3 concise sentences:\n\n"
        "{ai_response}"
    ),
    "elaborate": (
        "The user asked: \"{user_message}\"\n"
        "Your answer was: \"{ai_response_short}\"\n"
        "Elaborate with more detail, examples, and depth."
    ),
    "explain": (
        "Explain the following code clearly and concisely:\n\n"
        "{ai_response}"
    ),
    "show_steps": (
        "Rewrite the following as a clear numbered step-by-step:\n\n"
        "{ai_response}"
    ),
    "table_fmt": (
        "Reformat the following comparison into a clear table:\n\n"
        "{ai_response}"
    ),
    "recommend": (
        "From the following comparison, give a clear recommendation "
        "with brief justification:\n\n{ai_response}"
    ),
    "fb_improve": (
        "The user asked: \"{user_message}\"\n"
        "Your previous answer was rated poorly. "
        "Provide a significantly improved answer."
    ),
    # Special handlers (no prompt template)
    "copy_code": None,
    "approve": None,
    "alternative": None,
    "cancel": None,
    "fb_up": None,
    "fb_down": None,
}


class CallbackHandler:
    """Handle Telegram callback_query events (inline button presses)."""

    def __init__(self, channel: "TelegramChannel"):
        self.channel = channel
        self.store = get_callback_store()

    async def handle(self, callback_query: Dict[str, Any]) -> None:
        cb_id = callback_query.get("id", "")
        cb_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        user = callback_query.get("from", {})
        user_id = user.get("id")

        if not chat_id or not cb_data:
            await self._answer(cb_id, "⚠️ Invalid callback")
            return

        # ── Model switcher callbacks (ms_*) — no store needed ──
        if cb_data.startswith("ms_"):
            await self._handle_model_switch(cb_id, cb_data, chat_id, message_id, user_id)
            return

        if not chat_id or not cb_data:
            await self._answer(cb_id, "⚠️ Invalid callback")
            return

        entry = self.store.get(cb_data)
        if not entry:
            await self._answer(cb_id, "⏳ Button expired")
            return

        action = entry.action
        logger.info("Callback: action=%s user=%s", action, user_id)

        # ── Feedback (acknowledge only) ──
        if action == "fb_up":
            await self._answer(cb_id, "👍 Thanks!")
            return
        if action == "fb_down":
            await self._answer(cb_id, "👎 Noted, I'll do better")
            return

        # ── Approval actions ──
        if action == "approve":
            await self._answer(cb_id, "✅ Approved")
            # TODO: route approval to gateway approval manager
            return
        if action == "alternative":
            await self._answer(cb_id, "🔀 Using alternative")
            return
        if action == "cancel":
            await self._answer(cb_id, "❌ Cancelled")
            return

        # ── Copy code ──
        if action == "copy_code":
            code_blocks = _CODE_BLOCK.findall(entry.ai_response)
            if code_blocks:
                code_text = "\n\n".join(
                    block.strip("`").strip() for block in code_blocks
                )
                await self._answer(cb_id, "📋 Code extracted")
                await self.channel.send_message(
                    chat_id, f"```\n{code_text[:3900]}\n```"
                )
            else:
                await self._answer(cb_id, "No code blocks found")
            return

        # ── AI-routed actions ──
        prompt_template = _ACTION_PROMPTS.get(action)
        if prompt_template:
            await self._answer(cb_id, "🔍 Working on it…")
            typing_task = asyncio.create_task(self.channel._keep_typing(chat_id))
            try:
                prompt = prompt_template.format(
                    user_message=entry.user_message,
                    ai_response=entry.ai_response[:2500],
                    ai_response_short=entry.ai_response[:500],
                )
                response = await self._get_ai_response(prompt, user_id)
                if response:
                    builder = ResponseKeyboardBuilder(self.store)
                    keyboard = builder.build(
                        ai_response=response,
                        user_message=entry.user_message,
                        message_id=message_id,
                    )
                    await self.channel.send_message(chat_id, response, keyboard=keyboard)
                else:
                    await self.channel.send_message(chat_id, "❌ Couldn't generate a response.")
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass
            return

        await self._answer(cb_id, "⚠️ Unknown action")

    async def _answer(self, callback_id: str, text: str) -> None:
        await self.channel._api_call("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": False,
        })

    async def _handle_model_switch(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle model switcher inline buttons (ms_tier_*, ms_info)."""
        tier_map = {
            "ms_tier_small": ("small", "⚡ Small/Fast"),
            "ms_tier_big": ("big", "🧠 Large/Smart"),
            "ms_tier_coder": ("coder_big", "💻 Coding"),
            "ms_tier_auto": ("", "🔄 Auto (heuristic)"),
        }

        if cb_data in tier_map:
            tier, label = tier_map[cb_data]
            # Persist in channel's per-user prefs
            if tier:
                self.channel._user_model_prefs[user_id] = tier
            else:
                self.channel._user_model_prefs.pop(user_id, None)

            # Get model name for confirmation
            model_name = ""
            try:
                from navig.agent.ai_client import get_ai_client
                client = get_ai_client()
                router = client.model_router
                if router and tier:
                    slot = router.cfg.slot_for_tier(tier)
                    model_name = f" — {slot.model} ({slot.provider})"
            except Exception:
                pass

            await self._answer(cb_id, f"✅ Switched to {label}")

            # Update the original message with refreshed keyboard
            try:
                await self.channel._handle_models_command(chat_id, user_id)
            except Exception:
                pass

            # Send brief confirmation
            await self.channel.send_message(
                chat_id,
                f"✅ Model preset: *{label}*{model_name}\n"
                f"All your messages will now use this tier.",
                parse_mode="Markdown",
            )
            return

        if cb_data == "ms_info":
            # Show full routing table
            try:
                from navig.agent.ai_client import get_ai_client
                client = get_ai_client()
                router = client.model_router
                if router and router.is_active:
                    table = router.models_table()
                    await self._answer(cb_id, "📊 Full table")
                    await self.channel.send_message(
                        chat_id,
                        f"```\n{table}\n```",
                        parse_mode="Markdown",
                    )
                else:
                    await self._answer(cb_id, "Routing not active")
            except Exception as e:
                await self._answer(cb_id, f"Error: {e}")
            return

        await self._answer(cb_id, "⚠️ Unknown model action")

    async def _get_ai_response(self, prompt: str, user_id: int) -> Optional[str]:
        if self.channel.on_message:
            try:
                return await self.channel.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=prompt,
                    metadata={"chat_id": 0, "user_id": user_id},
                )
            except Exception as e:
                logger.error("AI callback failed: %s", e)
        return None
