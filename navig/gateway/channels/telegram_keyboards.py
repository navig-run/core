"""
Telegram Inline Keyboard System for NAVIG Gateway — v2

Three keyboard profiles (max 2 rows, max 3 buttons/row):
  action   — NAVIG proposes something: [Approve] [Reject] [Details]
  expand   — Response was truncated:   [Show more] [Open in Deck]
  feedback — Normal reply:             [👍] [👎]
  none     — Greetings, acks, short conversational (no keyboard)

Callback data schema:  action:hash
  action = button action type
  hash   = 6-char SHA-256 key into CallbackStore
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

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

    ACTION = "action"  # Approve/Reject/Details
    EXPAND = "expand"  # Show more / Open in Deck
    FEEDBACK = "feedback"  # 👍 👎
    NONE = "none"  # No keyboard


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
    # sha256 used here for non-security short-key generation (callback data deduplication)
    return hashlib.sha256(text.encode()).hexdigest()[:length]


@dataclass
class CallbackEntry:
    """Stored context for a callback button."""

    action: str
    user_message: str
    ai_response: str
    category: str
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class CallbackStore:
    """
    Bounded in-memory store: callback_data key → full context.
    Telegram limits callback_data to 64 bytes, so we store context here.
    """

    def __init__(self, max_entries: int = 500):
        self._store: dict[str, CallbackEntry] = {}
        self._max = max_entries

    def put(self, key: str, entry: CallbackEntry) -> None:
        if len(self._store) >= self._max:
            items = sorted(self._store.items(), key=lambda kv: kv[1].created_at)
            to_remove = len(items) // 5 or 1
            for k, _ in items[:to_remove]:
                del self._store[k]
        self._store[key] = entry

    def get(self, key: str) -> CallbackEntry | None:
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

    def __init__(self, store: CallbackStore | None = None):
        self.store = store or get_callback_store()

    def build(
        self,
        ai_response: str,
        user_message: str = "",
        message_id: int = 0,
        *,
        profile_override: str | None = None,
        approval_actions: list[dict[str, str]] | None = None,
    ) -> list[list[dict[str, str]]] | None:
        """
        Build inline keyboard for an AI response.

        Args:
            ai_response: The AI-generated text.
            user_message: The original user message.
            message_id: Telegram message ID.
            profile_override: Force a profile ("action"/"expand"/"feedback"/"none").
            approval_actions: For action profile — list of {"label": ..., "action": ...}
                with optional "request_id" to bind callback responses to an approval request.

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
        rows: list[list[dict[str, str]]] = []

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
        extra: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        label = text[:MAX_BUTTON_TEXT]
        cb_key = f"{action}:{msg_hash}"
        if len(cb_key) > MAX_CALLBACK_DATA:
            cb_key = cb_key[:MAX_CALLBACK_DATA]

        self.store.put(
            cb_key,
            CallbackEntry(
                action=action,
                user_message=user_message,
                ai_response=ai_response[:3000],
                category=classify_response(ai_response).value,
                extra=extra or {},
            ),
        )
        return {"text": label, "callback_data": cb_key}

    def _build_action_rows(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
        category: ContentCategory,
        approval_actions: list[dict[str, str]] | None = None,
    ) -> list[list[dict[str, str]]]:
        """Action profile: [Approve] [Alternative] [Cancel] or custom."""
        if approval_actions:
            row = []
            for item in approval_actions[:MAX_BUTTONS_PER_ROW]:
                request_id = str(item.get("request_id", "")).strip()
                extra = {"request_id": request_id} if request_id else None
                row.append(
                    self._make_button(
                        item["label"],
                        item["action"],
                        msg_hash,
                        user_message,
                        ai_response,
                        extra=extra,
                    )
                )
            return [row]

        is_ru = bool(re.search(r"[А-Яа-я]", ai_response))
        # Default approval pattern
        return [
            [
                self._make_button(
                    "✅ Принять" if is_ru else "✅ Approve",
                    "approve",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "🔀 Альтернатива" if is_ru else "🔀 Alternative",
                    "alternative",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "❌ Отмена" if is_ru else "❌ Cancel",
                    "cancel",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
            ]
        ]

    def _build_expand_rows(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
        category: ContentCategory,
    ) -> list[list[dict[str, str]]]:
        """
        Expand profile: context-aware row 1 + optional feedback row 2.

        Row 1 adapts to content:
          CODE  → [Explain] [Copy code]
          HOWTO → [Summarize] [Show steps]
          Other → [Summarize] [Go deeper]
        Row 2 → [👍] [👎] (always)
        """
        # Minimal language detection check based on cyrillic characters
        is_ru = bool(re.search(r"[А-Яа-я]", ai_response))

        # Row 1 — contextual actions
        if category == ContentCategory.CODE:
            row1 = [
                self._make_button(
                    "🔍 Объяснить" if is_ru else "🔍 Explain",
                    "explain",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "📋 Копировать" if is_ru else "📋 Copy code",
                    "copy_code",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
            ]
        elif category == ContentCategory.HOWTO:
            row1 = [
                self._make_button(
                    "📋 Кратко" if is_ru else "📋 Summarize",
                    "summarize",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "📝 По шагам" if is_ru else "📝 Show steps",
                    "show_steps",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
            ]
        elif category == ContentCategory.COMPARISON:
            row1 = [
                self._make_button(
                    "📊 Сравнить" if is_ru else "📊 Compare",
                    "table_fmt",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "✅ Рекомендовать" if is_ru else "✅ Recommend",
                    "recommend",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
            ]
        else:
            row1 = [
                self._make_button(
                    "📋 Кратко" if is_ru else "📋 Summarize",
                    "summarize",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "🔍 Подробнее" if is_ru else "🔍 Go deeper",
                    "elaborate",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
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
    ) -> list[list[dict[str, str]]]:
        """Feedback profile: just [👍] [👎]."""
        return [
            [
                self._make_button("👍", "fb_up", msg_hash, user_message, ai_response),
                self._make_button("👎", "fb_down", msg_hash, user_message, ai_response),
            ]
        ]


# ────────────────────────────────────────────────────────────────
# CallbackHandler
# ────────────────────────────────────────────────────────────────

_ACTION_PROMPTS: dict[str, str] = {
    "regen": (
        'The user asked: "{user_message}"\n'
        "Your previous answer was not satisfactory. "
        "Provide a better, more complete answer."
    ),
    "summarize": ("Summarize the following in 2-3 concise sentences:\n\n{ai_response}"),
    "elaborate": (
        'The user asked: "{user_message}"\n'
        'Your answer was: "{ai_response_short}"\n'
        "Elaborate with more detail, examples, and depth."
    ),
    "explain": ("Explain the following code clearly and concisely:\n\n{ai_response}"),
    "show_steps": ("Rewrite the following as a clear numbered step-by-step:\n\n{ai_response}"),
    "table_fmt": ("Reformat the following comparison into a clear table:\n\n{ai_response}"),
    "recommend": (
        "From the following comparison, give a clear recommendation "
        "with brief justification:\n\n{ai_response}"
    ),
    "fb_improve": (
        'The user asked: "{user_message}"\n'
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

# ── Settings menu helpers ─────────────────────────────────────────────────────


def _audio_header_text(session: Any) -> str:
    """Header text for the /audio panel — shows reply routing at a glance."""
    tts_labels = {
        "auto": "Auto",
        "google_cloud": "Google",
        "edge": "Edge TTS",
        "openai": "OpenAI",
        "deepgram": "Deepgram",
    }
    voice_mode = getattr(session, "voice_response_to_voice", "text")
    text_mode = getattr(session, "voice_response_to_text", "text")
    grp = "on" if getattr(session, "voice_in_groups", False) else "off"
    tts = tts_labels.get(getattr(session, "tts_provider", "auto"), "Auto")

    return (
        "🔊 *Audio Settings*\n\n"
        f"Voice input → `{voice_mode}` · Text input → `{text_mode}`\n"
        f"Groups `{grp}` · Provider `{tts}`"
    )


# Backward-compat alias used in telegram.py import
_settings_header_text = _audio_header_text


def _settings_hub_text(session: Any) -> str:
    """Header text for the /settings hub panel."""
    try:
        from navig.agent.soul import MOOD_REGISTRY

        focus = getattr(session, "focus_mode", "balance")
        mp = MOOD_REGISTRY.get(focus)
        focus_label = f"{mp.emoji} {focus}" if mp else focus
    except Exception:
        focus_label = getattr(session, "focus_mode", "balance")

    tts_p = getattr(session, "tts_provider", "auto")
    vr = "on" if getattr(session, "voice_replies", False) else "off"
    ai_m = getattr(session, "ai_mode", "") or "auto"
    return (
        "⚙️ *NAVIG Settings*\n\n"
        f"Focus `{focus_label}` · AI mode `{ai_m}`\n"
        f"Voice replies `{vr}` · TTS `{tts_p}`\n\n"
        "_Tap a section — /voice for full audio settings_"
    )


def build_audio_keyboard(session: Any) -> list[list[dict[str, Any]]]:
    """Inline keyboard rows for /audio — input/output voice routing."""

    def _on(active: bool) -> str:
        return " ●" if active else ""

    voice_mode = getattr(session, "voice_response_to_voice", "text")
    text_mode = getattr(session, "voice_response_to_text", "text")
    grp_on = getattr(session, "voice_in_groups", False)
    tts_p = getattr(session, "tts_provider", "auto")

    provider_labels = {
        "auto": "Auto",
        "google_cloud": "Google",
        "edge": "Edge TTS",
        "openai": "OpenAI",
        "deepgram": "Deepgram",
    }

    return [
        [
            {
                "text": f"🎤 Voice → Text{_on(voice_mode == 'text')}",
                "callback_data": "st_vrv_text",
            },
            {
                "text": f"🎤 Voice → Voice{_on(voice_mode == 'voice')}",
                "callback_data": "st_vrv_voice",
            },
            {
                "text": f"🎤 Voice → Auto{_on(voice_mode == 'auto')}",
                "callback_data": "st_vrv_auto",
            },
        ],
        [
            {
                "text": f"⌨️ Text → Text{_on(text_mode == 'text')}",
                "callback_data": "st_vrt_text",
            },
            {
                "text": f"⌨️ Text → Voice{_on(text_mode == 'voice')}",
                "callback_data": "st_vrt_voice",
            },
            {
                "text": f"⌨️ Text → Off{_on(text_mode == 'off')}",
                "callback_data": "st_vrt_off",
            },
        ],
        [
            {
                "text": f"{'👥' if grp_on else '💬'} Group voice {'ON' if grp_on else 'OFF'}",
                "callback_data": "st_grp",
            },
        ],
        [
            {
                "text": f"🎙 Provider: {provider_labels.get(tts_p, 'Auto')}",
                "callback_data": "st_goto_voice",
            },
        ],
        [
            {"text": "🎙 TTS Providers", "callback_data": "st_goto_voice"},
            {"text": "⚙️ All settings", "callback_data": "st_goto_settings"},
            {"text": "✕ close", "callback_data": "st_close"},
        ],
    ]


# Backward-compat alias
build_settings_keyboard = build_audio_keyboard


def build_settings_hub_keyboard(session: Any = None) -> list[list[dict[str, Any]]]:
    """Main /settings hub — pro inline navigation panel."""
    return [
        [
            {"text": "🎙  Voice settings", "callback_data": "st_goto_audio"},
            {"text": "🤖  Providers & Models", "callback_data": "st_goto_providers"},
        ],
        [
            {"text": "🛠  Debug", "callback_data": "st_goto_debug"},
        ],
        [
            {"text": "✕  Close", "callback_data": "st_close"},
        ],
    ]


class CallbackHandler:
    """Handle Telegram callback_query events (inline button presses)."""

    def __init__(self, channel: TelegramChannel):
        self.channel = channel
        self.store = get_callback_store()
        self._answered_callback_ids: set[str] = set()

    async def handle(self, callback_query: dict[str, Any]) -> None:
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

        try:
            if cb_data.startswith("nav:"):
                await self._handle_navigation_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Model switcher callbacks (ms_*) — no store needed ──
            if cb_data.startswith("task:"):
                await self._handle_task_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            if cb_data.startswith("ms_"):
                await self._handle_model_switch(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Provider model picker callbacks (pm_*) — no store needed ──
            if cb_data.startswith("pm_"):
                await self._handle_provider_model_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Provider hub callbacks (prov_*) — no store needed ──
            if cb_data.startswith("prov_"):
                await self._handle_provider_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Settings callbacks (st_*) — no store needed ──
            if cb_data.startswith("st_"):
                await self._handle_settings_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Debug callbacks (dbg_*) — no store needed ──
            if cb_data.startswith("dbg_"):
                await self._handle_debug_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Trace action buttons (trace_*) — no store needed ──
            if cb_data.startswith("trace_"):
                await self._handle_trace_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Heard action cards (heard_*) — no store needed ──
            if cb_data.startswith("heard_"):
                await self._handle_heard_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── NL confirm/cancel callbacks (nl_*) — no store needed ──
            if cb_data.startswith("nl_"):
                handler = getattr(self.channel, "_handle_nl_callback", None)
                if handler:
                    self._answered_callback_ids.add(cb_id)
                    await handler(cb_id, cb_data, chat_id, user_id)
                else:
                    await self._answer(cb_id, "⚠️ Action unavailable")
                return

            # ── Audio deep menu (audio:*) — no store needed ──
            if cb_data.startswith("audio:"):
                try:
                    from navig.gateway.channels.audio_menu import handle_audio_callback

                    await handle_audio_callback(
                        self.channel, cb_id, cb_data, chat_id, message_id, user_id
                    )
                except Exception as _amc_err:
                    logger.warning("Audio menu callback error: chat_id=%s callback=%s err=%s", chat_id, cb_data, _amc_err)
                    await self._answer(cb_id, "\u26a0\ufe0f Audio menu error")
                return

            # ── Audio file action buttons (audmsg:*) — no store needed ──
            if cb_data.startswith("audmsg:"):
                await self._handle_audio_file_callback(cb_id, cb_data, chat_id, message_id, user_id)
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
                # AUDIT DECISION:
                # Is this the correct implementation? Yes — delegate to channel-level approval
                # responder that enforces request ownership and channel checks.
                # Does it break any existing callers? No — if no responder is configured, we
                # return a clear unavailability message instead of silently succeeding.
                # Is there a simpler alternative? Yes, but acknowledging without routing is unsafe.
                _, message = await self._handle_approval_action(
                    entry=entry,
                    user_id=user_id,
                    approved=True,
                )
                await self._answer(cb_id, message)
                return
            if action == "alternative":
                await self._answer(cb_id, "🔀 Using alternative")
                return
            if action == "cancel":
                _, message = await self._handle_approval_action(
                    entry=entry,
                    user_id=user_id,
                    approved=False,
                )
                await self._answer(cb_id, message)
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
                try:
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
                            await self.channel.send_message(
                                chat_id, response, keyboard=keyboard
                            )
                        else:
                            await self.channel.send_message(
                                chat_id, "❌ Couldn't generate a response."
                            )
                    finally:
                        typing_task.cancel()
                        try:
                            await typing_task
                        except asyncio.CancelledError:
                            pass  # task cancelled; expected during shutdown
                except Exception:
                    raise
                return

            # ── Auto-Heal action buttons (heal_*) ───────────────────────────────────
            if action.startswith("heal_"):
                mixin = self.channel
                if hasattr(mixin, "_dispatch_heal_callback"):
                    await mixin._dispatch_heal_callback(
                        action=action,
                        cb_key=cb_data,
                        chat_id=chat_id,
                        user_id=user_id,
                        cb_id=cb_id,
                    )
                else:
                    await self._answer(cb_id, "⚠️ Auto-Heal not available")
                return

            await self._answer(cb_id, "⚠️ Unknown action")
        except Exception as exc:
            logger.exception("Callback handling failed: chat_id=%s callback=%s err=%s", chat_id, cb_data, exc)
            await self._show_callback_error_screen(chat_id, message_id, cb_data)
            await self._answer(cb_id, "❌ Failed — try again")
        finally:
            if cb_id and cb_id not in self._answered_callback_ids:
                await self._answer(cb_id, "")
            logger.debug(
                "callback_ack chat_id=%s callback=%s answered=%s",
                chat_id,
                cb_data,
                cb_id in self._answered_callback_ids,
            )
            self._answered_callback_ids.discard(cb_id)

    async def _show_callback_error_screen(self, chat_id: int, message_id: int, cb_data: str) -> None:
        if not message_id:
            await self.channel.send_message(
                chat_id,
                "⚠️ Something went wrong. Use /start to return to menu.",
                parse_mode=None,
            )
            return
        await self.channel._api_call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "⚠️ Something went wrong. You can safely return to the main menu.",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "🏠 Return to Menu", "callback_data": "nav:home"}],
                    ]
                },
            },
        )

    async def _handle_navigation_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        parts = cb_data.split(":")
        action = parts[1] if len(parts) > 1 else ""
        target = parts[2] if len(parts) > 2 else ""

        if action == "open" and target:
            await self._answer(cb_id, "")
            nav = getattr(self.channel, "navigateTo", None)
            if nav:
                await nav(
                    chat_id=chat_id,
                    screen=target,
                    user_id=user_id,
                    message_id=message_id,
                )
            return

        if action == "back":
            await self._answer(cb_id, "")
            back = getattr(self.channel, "navigateBack", None)
            if back:
                await back(chat_id=chat_id, user_id=user_id, message_id=message_id)
            return

        if action in {"home", "cancel"}:
            await self._answer(cb_id, "")
            nav = getattr(self.channel, "navigateTo", None)
            if nav:
                await nav(
                    chat_id=chat_id,
                    screen="main",
                    user_id=user_id,
                    message_id=message_id,
                )
            return

        await self._answer(cb_id, "⚠️ Unknown navigation")

    async def _answer(self, callback_id: str, text: str, show_alert: bool = False) -> None:
        if not callback_id:
            return
        if callback_id in self._answered_callback_ids:
            return
        self._answered_callback_ids.add(callback_id)
        await self.channel._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert,
            },
        )

    async def _handle_approval_action(
        self,
        entry: CallbackEntry,
        user_id: int | None,
        approved: bool,
    ) -> tuple[bool, str]:
        """Route Telegram approval callbacks through channel-level approval responder."""
        if not user_id:
            return False, "⚠️ Missing user context."

        responder = getattr(self.channel, "on_approval_response", None)
        if not responder:
            return False, "⚠️ Approval system unavailable."

        request_id = ""
        if isinstance(entry.extra, dict):
            request_id = str(entry.extra.get("request_id", "")).strip()

        try:
            success, message = await responder(
                int(user_id),
                approved,
                request_id or None,
            )
            if success:
                return True, message
            if approved:
                return False, message or "⚠️ Approval request could not be completed."
            # Deny/cancel should stay user-friendly when there is no pending request.
            return False, message or "❌ Cancelled"
        except Exception as e:
            logger.error("Approval callback failed: %s", e)
            if approved:
                return False, "⚠️ Approval system unavailable."
            return False, "❌ Cancelled"

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
            if hasattr(self.channel, "_set_user_tier_pref"):
                self.channel._set_user_tier_pref(chat_id, user_id, tier)
            else:
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
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

            await self._answer(cb_id, f"✅ Switched to {label}")

            # Update the original message with refreshed keyboard
            try:
                await self.channel._handle_models_command(
                    chat_id,
                    user_id,
                    message_id=message_id,
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
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

        if cb_data in ("ms_providers", "open_providers"):
            await self._answer(cb_id, "🔌 Providers")
            await self.channel._handle_providers(
                chat_id,
                user_id,
                message_id=message_id,
            )
            return

        # ── Provider-force shortcuts (ms_prov_*) — redirect to provider model picker ──
        prov_map = {
            "ms_prov_xai": "xai",
            "ms_prov_openai": "openai",
        }
        if cb_data in prov_map:
            prov_id = prov_map[cb_data]
            await self._answer(cb_id, "")
            await self.channel._show_provider_model_picker(chat_id, prov_id)
            return

        await self._answer(cb_id, "⚠️ Unknown model action")

    async def _handle_provider_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle provider hub inline buttons (prov_* callbacks)."""
        if cb_data == "prov_close":
            await self._answer(cb_id, "✖ Closed")
            try:
                await self.channel._api_call(
                    "deleteMessage",
                    {"chat_id": chat_id, "message_id": message_id},
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            return

        if cb_data == "prov_noai":
            if hasattr(self.channel, "_set_one_shot_noai"):
                self.channel._set_one_shot_noai(user_id)
            else:
                self.channel._user_model_prefs[user_id] = "noai"
            await self._answer(cb_id, "🚫 Raw mode armed for next message")
            try:
                await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            except TypeError:
                await self.channel._handle_providers(chat_id)
            return

        if cb_data == "prov_bridge":
            online, url = await self.channel._probe_bridge_grid()
            status = f"online at {url}" if online else f"offline ({url})"
            await self._answer(cb_id, f"⚡ Bridge Grid: {status}", show_alert=True)
            return

        if cb_data == "prov_back":
            await self._answer(cb_id, "")
            try:
                await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            except TypeError:
                await self.channel._handle_providers(chat_id)
            return

        # ── Pagination: prov_page_{prov_id}_{page} ──────────────────────────
        if cb_data.startswith("prov_page_"):
            rest = cb_data[len("prov_page_"):]
            # prov_id may contain underscores; page number is always the last segment
            parts = rest.rsplit("_", 1)
            if len(parts) == 2:
                prov_id_p, page_str = parts
                try:
                    page_num = int(page_str)
                except ValueError:
                    page_num = 0
                await self._answer(cb_id, "")
                try:
                    await self.channel._show_provider_model_picker(
                        chat_id,
                        prov_id_p,
                        page=page_num,
                        selected_tier="s",
                        message_id=message_id,
                    )
                except TypeError:
                    try:
                        await self.channel._show_provider_model_picker(
                            chat_id, prov_id_p, page_num
                        )
                    except TypeError:
                        await self.channel._show_provider_model_picker(chat_id, prov_id_p)
            else:
                await self._answer(cb_id, "⚠️ Bad page callback")
            return

        # ── Activate provider: prov_activate_{prov_id} ──────────────────────
        if cb_data.startswith("prov_activate_"):
            prov_id_a = cb_data[len("prov_activate_"):]
            try:
                from navig.providers.registry import get_provider
                from navig.agent.ai_client import get_ai_client

                manifest = get_provider(prov_id_a)
                router = get_ai_client().model_router
                if not router or not router.is_active:
                    await self._answer(
                        cb_id,
                        "⚠️ Hybrid routing disabled — set routing.enabled: true and restart",
                        show_alert=True,
                    )
                    return

                models = []
                if hasattr(self.channel, "_resolve_provider_models"):
                    models = await self.channel._resolve_provider_models(prov_id_a, manifest=manifest)

                if not models:
                    prov_label = manifest.display_name if manifest else prov_id_a
                    await self._answer(
                        cb_id,
                        f"⚠️ No models configured for {prov_label}",
                        show_alert=True,
                    )
                    return

                defaults = self.channel._select_curated_tier_defaults(prov_id_a, models)
                for tier in ("small", "big", "coder_big"):
                    slot = router.cfg.slot_for_tier(tier)
                    slot.provider = prov_id_a
                    slot.model = defaults[tier]

                if hasattr(self.channel, "_persist_hybrid_router_assignments"):
                    self.channel._persist_hybrid_router_assignments(router.cfg)

                prov_label = manifest.display_name if manifest else prov_id_a
                await self._answer(
                    cb_id,
                    f"✅ {manifest.emoji if manifest else ''} {prov_label} activated for all tiers",
                    show_alert=True,
                )
                await self.channel.send_message(
                    chat_id,
                    f"✅ <b>{prov_label}</b> is now active.\n"
                    f"Big: <code>{defaults['big']}</code> · Small: <code>{defaults['small']}</code> · "
                    f"Code: <code>{defaults['coder_big']}</code>\n"
                    f"<i>Saved to global config. Use /models to verify or adjust tiers.</i>",
                    parse_mode="HTML",
                )
            except Exception as exc:
                await self._answer(cb_id, f"⚠️ Activation failed: {exc}", show_alert=True)
            return

        # Providers that open a full model↔tier picker
        # Static map for known providers + dynamic fallback via registry
        picker_map: dict = {
            "prov_openrouter": "openrouter",
            "prov_github": "github_models",
            "prov_github_models": "github_models",
            "prov_nvidia": "nvidia",
            "prov_ollama": "ollama",
            "prov_xai": "xai",
            "prov_openai": "openai",
            "prov_anthropic": "anthropic",
            "prov_google": "google",
            "prov_groq": "groq",
            "prov_mistral": "mistral",
            "prov_llamacpp": "llamacpp",
            "prov_airllm": "airllm",
        }
        if cb_data in picker_map:
            prov_id = picker_map[cb_data]
            try:
                await self.channel._show_provider_model_picker(
                    chat_id,
                    prov_id,
                    page=0,
                    selected_tier="s",
                    message_id=message_id,
                )
                await self._answer(cb_id, "")
            except TypeError as exc:
                err = str(exc)
                signature_mismatch = (
                    "unexpected keyword argument" in err
                    or "positional argument" in err
                    or "required positional argument" in err
                )
                if signature_mismatch:
                    await self.channel._show_provider_model_picker(chat_id, prov_id=prov_id)
                    await self._answer(cb_id, "")
                else:
                    logger.warning(
                        "Provider picker failed for %s: %s",
                        prov_id,
                        exc,
                    )
                    await self._answer(cb_id, f"⚠️ Couldn't open {prov_id} picker", show_alert=True)
                    try:
                        await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
                    except TypeError:
                        await self.channel._handle_providers(chat_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Provider picker failed for %s: %s",
                    prov_id,
                    exc,
                )
                await self._answer(cb_id, f"⚠️ Couldn't open {prov_id} picker", show_alert=True)
                try:
                    await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
                except TypeError:
                    await self.channel._handle_providers(chat_id)
            return

        # Deepgram: STT only, no LLM routing
        if cb_data == "prov_deepgram":
            await self._answer(
                cb_id,
                "🎙 Deepgram STT — speech-to-text only · not used for LLM routing",
                show_alert=True,
            )
            return

        # Generic fallback: look up provider in registry for a live description
        prov_id = cb_data[len("prov_") :]  # strip "prov_" prefix
        try:
            from navig.providers.registry import get_provider
            from navig.providers.verifier import verify_provider

            manifest = get_provider(prov_id)
            if manifest:
                result = verify_provider(manifest)
                if result.key_detected or not manifest.requires_key:
                    key_status = "✅ configured"
                else:
                    env_hint = " or ".join(manifest.env_vars[:2]) if manifest.env_vars else "—"
                    vault_hint = manifest.vault_keys[0] if manifest.vault_keys else "—"
                    key_status = f"⬜ not found — set {env_hint}" + (
                        f" or vault '{vault_hint}'" if vault_hint != "—" else ""
                    )
                toast = (
                    f"{manifest.emoji} {manifest.display_name} — "
                    f"{manifest.description[:80]}{'…' if len(manifest.description) > 80 else ''} | "
                    f"Key: {key_status}"
                )
                await self._answer(cb_id, toast, show_alert=True)
                return
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        await self._answer(cb_id, f"Provider info unavailable for '{prov_id}'")

    async def _handle_provider_model_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle tier-first provider picker callbacks.

        Supported callback formats:
        - `pmv_{prov_id}_{tier_code}_{page}`: switch viewed tier
        - `pmp_{prov_id}_{page}_{tier_code}`: paginate while keeping tier
        - `pms_{prov_id}_{model_idx}_{tier_code}_{page}`: assign selected model to tier
        """
        if cb_data.startswith("pmv_"):
            rest = cb_data[4:]
            parts = rest.rsplit("_", 2)
            if len(parts) != 3:
                await self._answer(cb_id, "⚠️ Bad tier switch callback")
                return
            prov_id, tier_code, page_str = parts
            try:
                page = int(page_str)
            except ValueError:
                page = 0
            await self._answer(cb_id, "")
            await self.channel._show_provider_model_picker(
                chat_id,
                prov_id,
                page=page,
                selected_tier=tier_code,
                message_id=message_id,
            )
            return

        if cb_data.startswith("pmp_"):
            rest = cb_data[4:]
            parts = rest.rsplit("_", 2)
            if len(parts) != 3:
                await self._answer(cb_id, "⚠️ Bad page callback")
                return
            prov_id, page_str, tier_code = parts
            try:
                page = int(page_str)
            except ValueError:
                page = 0
            await self._answer(cb_id, "")
            await self.channel._show_provider_model_picker(
                chat_id,
                prov_id,
                page=page,
                selected_tier=tier_code,
                message_id=message_id,
            )
            return

        if not cb_data.startswith("pms_"):
            await self._answer(cb_id, "⚠️ Unknown model action")
            return

        rest = cb_data[4:]
        parts = rest.rsplit("_", 3)
        if len(parts) != 4:
            await self._answer(cb_id, "⚠️ Bad assignment callback")
            return
        prov_id, model_idx_str, tier_code, page_str = parts

        tier_map: dict = {
            "s": ("small", "⚡ Small"),
            "b": ("big", "🧠 Big"),
            "c": ("coder_big", "💻 Code"),
        }
        if tier_code not in tier_map:
            await self._answer(cb_id, "⚠️ Unknown tier code")
            return
        tier, tier_label = tier_map[tier_code]

        try:
            model_idx = int(model_idx_str)
        except ValueError:
            await self._answer(cb_id, "⚠️ Bad model index")
            return
        try:
            page = int(page_str)
        except ValueError:
            page = 0

        models: list = []
        try:
            from navig.providers.registry import get_provider

            manifest = get_provider(prov_id)
        except Exception:
            manifest = None

        if hasattr(self.channel, "_resolve_provider_models"):
            models = await self.channel._resolve_provider_models(prov_id, manifest=manifest)

        if model_idx >= len(models):
            await self._answer(cb_id, "⚠️ Model index out of range")
            return
        model = models[model_idx]

        # Update the live hybrid router config in-place (session-level override)
        try:
            from navig.agent.ai_client import get_ai_client

            router = get_ai_client().model_router
            if not router or not router.is_active:
                await self._answer(
                    cb_id,
                    "⚠️ Hybrid router not active",
                    show_alert=True,
                )
                await self.channel.send_message(
                    chat_id,
                    "⚠️ <b>Hybrid router not active</b>\n\n"
                    "Hybrid routing means Small/Big/Code can each use different provider:model slots.\n"
                    "To enable model-tier assignment, set this in your config:\n\n"
                    "<pre>routing:\n  enabled: true</pre>\n\n"
                    "Then restart NAVIG and try again.",
                    parse_mode="HTML",
                )
                return
            slot = router.cfg.slot_for_tier(tier)
            slot.provider = prov_id
            slot.model = model
            if hasattr(self.channel, "_persist_hybrid_router_assignments"):
                self.channel._persist_hybrid_router_assignments(router.cfg)
            await self._answer(cb_id, f"✅ {tier_label} → {model[:40]}")
            await self.channel._show_provider_model_picker(
                chat_id,
                prov_id,
                page=page,
                selected_tier=tier_code,
                message_id=message_id,
            )
        except Exception as e:
            await self._answer(cb_id, f"Error: {e}", show_alert=True)

    async def _handle_debug_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle debug button presses (dbg_* callbacks).
        dbg_trace → show /trace snapshot for the pressing user.
        """
        await self._answer(cb_id, "🔍 Fetching trace...")
        if cb_data == "dbg_trace":
            try:
                await self.channel._handle_trace(chat_id, user_id)
            except Exception as exc:
                logger.debug("Debug trace callback failed: %s", exc)
                await self.channel.send_message(chat_id, "⚠️ Trace unavailable.", parse_mode=None)
        else:
            await self.channel.send_message(
                chat_id, f"⚠️ Unknown debug action: `{cb_data}`", parse_mode=None
            )

    async def _handle_trace_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle /trace action buttons: refresh, providers, model, close."""
        if cb_data == "trace_refresh":
            await self._answer(cb_id, "🔄 Refreshing...")
            try:
                await self.channel._handle_trace(chat_id, user_id)
            except Exception as exc:
                logger.debug("Trace refresh failed: %s", exc)
        elif cb_data == "trace_providers":
            await self._answer(cb_id, "")
            await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
        elif cb_data == "trace_model":
            await self._answer(cb_id, "")
            await self.channel._handle_models_command(chat_id, user_id, message_id=message_id)
        elif cb_data == "trace_close":
            try:
                await self.channel._api_call(
                    "deleteMessage",
                    {"chat_id": chat_id, "message_id": message_id},
                )
                await self._answer(cb_id, "")
            except Exception:
                await self._answer(cb_id, "")
        else:
            await self._answer(cb_id, "")

    async def _handle_audio_file_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle action buttons for received audio/music files (audmsg:{action}:{file_id})."""
        import asyncio as _asyncio

        parts = cb_data.split(":", 2)  # ["audmsg", action, short_id]
        action = parts[1] if len(parts) > 1 else ""
        short_id = parts[2] if len(parts) > 2 else ""

        try:
            from navig.gateway.channels.telegram_voice import _af_cache

            meta = _af_cache.get(short_id, {})
        except Exception:
            meta = {}

        file_id = meta.get("file_id", short_id)
        is_speech = meta.get("is_speech", False)

        if action == "dismiss":
            try:
                await self.channel._api_call(
                    "deleteMessage", {"chat_id": chat_id, "message_id": message_id}
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            await self._answer(cb_id, "")
            return

        if action == "transcribe":
            await self._answer(cb_id, "🎤 Transcribing...")
            from navig.gateway.channels.task_card import (
                StepState,
                make_task,
                send_task_card,
                update_task_card,
            )

            view = make_task(
                [
                    ("download", "Downloading audio file"),
                    ("stt", "Running speech-to-text"),
                    ("finalize", "Finalizing transcript"),
                ],
                title="🎤 Transcribing Audio...",
            )

            # Use raw message store if context data not easily available
            if not getattr(self.channel, "task_views", None):
                self.channel.task_views = {}

            await send_task_card(self.channel, chat_id, view)
            self.channel.task_views[view.message_id] = view

            try:
                transcript = await self.channel._transcribe_audio_file(
                    chat_id, file_id, is_voice=is_speech, task_view=view
                )
                if transcript:
                    view.set_step("finalize", StepState.DONE)
                    view.done = True
                    view.running = False
                    view.recompute_percent()
                    await update_task_card(self.channel, chat_id, view, force=True)

                    await self.channel.send_message(
                        chat_id,
                        f"📝 *Transcript:*\n{transcript}",
                        parse_mode="Markdown",
                    )
                else:
                    view.set_step("finalize", StepState.FAILED, "No speech found")
                    view.done = True
                    view.running = False
                    view.recompute_percent()
                    await update_task_card(self.channel, chat_id, view, force=True)

                    await self.channel.send_message(
                        chat_id,
                        "⚠️ Transcription failed — this file may not contain speech.",
                    )
            except Exception as exc:
                import logging

                logging.getLogger("navig").warning("audmsg:transcribe error: %s", exc)
                view.set_step("finalize", StepState.FAILED, str(exc)[:50])
                view.done = True
                view.running = False
                view.recompute_percent()
                await update_task_card(self.channel, chat_id, view, force=True)

                await self.channel.send_message(
                    chat_id,
                    "⚠️ Transcription failed — this file may not contain speech.",
                )
            return

        if action == "identify":
            await self._answer(cb_id, "🔍 Looking it up...")
            title = meta.get("title") or "an audio file"
            performer = meta.get("performer") or ""
            duration = int(meta.get("duration") or 0)
            mins, secs = divmod(duration, 60)

            prompt = f'The user sent an audio file titled "{title}"'
            if performer:
                prompt += f" by {performer}"
            if duration:
                prompt += f" ({mins}:{secs:02d} long)"
            prompt += ". Tell the user what you know about this track or artist in 2-3 short sentences. If you don't recognise it, say so honestly."

            try:
                from navig.llm_generate import llm_generate

                reply = await _asyncio.to_thread(
                    llm_generate,
                    messages=[{"role": "user", "content": prompt}],
                    mode="chat",
                )
                await self.channel.send_message(chat_id, reply or "Nothing found.")
            except Exception as exc:
                logger.warning("audmsg:identify error: %s", exc)
                await self.channel.send_message(chat_id, "⚠️ Couldn't look that up right now.")
            return

        if action == "info":
            await self._answer(cb_id, "ℹ️")
            title = meta.get("title") or "Unknown"
            performer = meta.get("performer") or "—"
            duration = int(meta.get("duration") or 0)
            file_size = int(meta.get("file_size") or 0)
            mime_type = meta.get("mime_type") or "—"
            mins, secs = divmod(duration, 60)
            dur_str = f"{mins}:{secs:02d}" if duration else "—"
            size_str = f"{file_size // 1024:,} KB" if file_size else "—"
            kind = "Voice recording" if is_speech else "Music file"
            info_text = (
                f"ℹ️ *File Info*\n"
                f"Title: {title}\n"
                f"Artist: {performer}\n"
                f"Duration: {dur_str}\n"
                f"Size: {size_str}\n"
                f"MIME: `{mime_type}`\n"
                f"Type: {kind}"
            )
            await self.channel.send_message(chat_id, info_text, parse_mode="Markdown")
            return

        if action == "lang":
            await self._answer(cb_id, "🌐 Detecting language...")
            try:
                transcript = await self.channel._transcribe_audio_file(
                    chat_id, file_id, is_voice=True
                )
                if transcript:
                    # Ask LLM what language the transcript is in
                    try:
                        from navig.llm_generate import llm_generate

                        prompt = f'What language is this text written in? Reply with only the language name.\n\n"{transcript[:500]}"'
                        lang_reply = await _asyncio.to_thread(
                            llm_generate,
                            messages=[{"role": "user", "content": prompt}],
                            mode="chat",
                        )
                        await self.channel.send_message(
                            chat_id,
                            f"🌐 Detected language: *{lang_reply or 'Unknown'}*",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        await self.channel.send_message(chat_id, "⚠️ Language detection failed.")
                else:
                    await self.channel.send_message(
                        chat_id, "⚠️ Could not transcribe audio for language detection."
                    )
            except Exception as exc:
                logger.warning("audmsg:lang error: %s", exc)
                await self.channel.send_message(chat_id, "⚠️ Language detection failed.")
            return

        await self._answer(cb_id, "")

    async def _handle_heard_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle voice Heard: action cards."""
        if cb_data == "heard_process":
            # The transcript is in the message text; just dismiss and let the pipeline run
            await self._answer(cb_id, "💡 Processing...")
        elif cb_data == "heard_retry":
            await self._answer(cb_id, "🔁 Send a new voice message to re-transcribe.")
        elif cb_data == "heard_edit":
            await self._answer(cb_id, "📝 Reply to the Heard: message with your edited text.")
        else:
            await self._answer(cb_id, "")

    async def _handle_settings_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle /settings inline button presses (st_* callbacks)."""
        is_group = chat_id < 0

        # Resolve session
        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            session = sm.get_or_create_session(chat_id, user_id, is_group)
        except Exception as exc:
            await self._answer(cb_id, f"⚠️ Session error: {exc}")
            return

        # Close / dismiss
        if cb_data == "st_close":
            await self._answer(cb_id, "✖ Settings closed")
            try:
                await self.channel._api_call(
                    "deleteMessage",
                    {
                        "chat_id": chat_id,
                        "message_id": message_id,
                    },
                )
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
            return

        # Map callback → (field, value) or toggle
        _TOGGLE = {
            "st_voice": "voice_enabled",
            "st_stt": "stt_enabled",
            "st_grp": "voice_in_groups",
            "st_vr": "voice_replies",
        }
        _SELECT = {
            "st_tts_a": ("tts_provider", "auto"),
            "st_tts_g": ("tts_provider", "google_cloud"),
            "st_tts_e": ("tts_provider", "edge"),
            "st_tts_o": ("tts_provider", "openai"),
            "st_tts_d": ("tts_provider", "deepgram"),
            "st_vrv_text": ("voice_response_to_voice", "text"),
            "st_vrv_voice": ("voice_response_to_voice", "voice"),
            "st_vrv_auto": ("voice_response_to_voice", "auto"),
            "st_vrt_text": ("voice_response_to_text", "text"),
            "st_vrt_voice": ("voice_response_to_text", "voice"),
            "st_vrt_off": ("voice_response_to_text", "off"),
            "st_mode_a": ("ai_mode", ""),
            "st_mode_t": ("ai_mode", "talk"),
            "st_mode_r": ("ai_mode", "reason"),
            "st_mode_c": ("ai_mode", "code"),
        }
        _TOAST = {
            "st_voice": lambda s: f"🔊 Voice {'ON' if s.voice_enabled else 'OFF'}",
            "st_stt": lambda s: f"🎙 Transcription {'ON' if s.stt_enabled else 'OFF'}",
            "st_grp": lambda s: f"👥 Group voice {'ON' if s.voice_in_groups else 'OFF'}",
            "st_vr": lambda s: f"🔊 Bot voice replies {'ON' if s.voice_replies else 'OFF'}",
            "st_tts_a": lambda _: "⚡ TTS: Auto (Edge → Google fallback)",
            "st_tts_g": lambda _: "☁️ TTS: Google Cloud",
            "st_tts_e": lambda _: "🌐 TTS: Edge TTS (Microsoft, no key needed)",
            "st_tts_o": lambda _: "🤖 TTS: OpenAI TTS-1",
            "st_tts_d": lambda _: "🎙 TTS: Deepgram (API key required)",
            "st_vrv_text": lambda _: "🎤 Voice messages will get text replies",
            "st_vrv_voice": lambda _: "🎤 Voice messages will get voice replies",
            "st_vrv_auto": lambda s: (
                f"🎤 Voice messages set to auto ({'voice' if s.voice_replies else 'text'} fallback)"
            ),
            "st_vrt_text": lambda _: "⌨️ Text messages will get text replies",
            "st_vrt_voice": lambda _: "⌨️ Text messages will get voice replies",
            "st_vrt_off": lambda _: "⌨️ Text-triggered replies are now off",
            "st_mode_a": lambda _: "🔄 AI mode: Auto",
            "st_mode_t": lambda _: "💬 AI mode: Talk",
            "st_mode_r": lambda _: "🧠 AI mode: Reason",
            "st_mode_c": lambda _: "💻 AI mode: Code",
        }

        # ── Navigation callbacks ───────────────────────────────────────────
        _NAV = {
            "st_goto_audio": "_handle_audio_menu",
            "st_goto_voice": "_handle_voice_menu",
            "st_goto_providers": "_handle_providers_and_models",
            "st_goto_focus": "_handle_mode_menu",
            "st_goto_model": "_handle_models_command",
            "st_goto_trace": "_handle_trace",
            "st_goto_debug": "_handle_debug",
            "st_goto_settings": "_handle_settings_hub",
        }
        if cb_data in _NAV:
            await self._answer(cb_id, "")
            method_name = _NAV[cb_data]
            method = getattr(self.channel, method_name, None)
            if method:
                try:
                    if cb_data in ("st_goto_model", "st_goto_trace"):
                        await method(chat_id, user_id)
                    elif cb_data == "st_goto_focus":
                        await method(chat_id, "", user_id=user_id)
                    else:
                        try:
                            await method(
                                chat_id,
                                user_id,
                                is_group,
                                message_id=message_id,
                            )
                        except TypeError:
                            await method(chat_id)
                except Exception as exc:
                    logger.debug("Nav callback %s failed: %s", cb_data, exc)
            return

        if cb_data in _TOGGLE:
            field = _TOGGLE[cb_data]
            setattr(session, field, not getattr(session, field, True))
        elif cb_data in _SELECT:
            field, value = _SELECT[cb_data]
            setattr(session, field, value)
        else:
            await self._answer(cb_id, "⚠️ Unknown setting")
            return

        # Persist
        try:
            sm._save_session(session)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        toast = _TOAST.get(cb_data, lambda _: "✅ Updated")(session)
        await self._answer(cb_id, toast)

        # Refresh the settings message in-place
        keyboard_rows = build_audio_keyboard(session)
        try:
            await self.channel._api_call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "text": _audio_header_text(session),
                    "parse_mode": "Markdown",
                    "reply_markup": {"inline_keyboard": keyboard_rows},
                },
            )
        except Exception as exc:
            logger.debug("Audio settings refresh failed: %s", exc)

    async def _get_ai_response(self, prompt: str, user_id: int) -> str | None:
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
