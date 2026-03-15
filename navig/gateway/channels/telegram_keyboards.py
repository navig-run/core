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
                request_id = str(item.get("request_id", "")).strip()
                extra = {"request_id": request_id} if request_id else None
                row.append(self._make_button(
                    item["label"],
                    item["action"],
                    msg_hash,
                    user_message,
                    ai_response,
                    extra=extra,
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

# ── Settings menu helpers ─────────────────────────────────────────────────────

def _settings_header_text(session: Any) -> str:
    """Header text for the /settings panel — shows current state at a glance."""
    tts_labels = {
        "auto": "Auto", "google_cloud": "Google Cloud",
        "edge": "Edge TTS", "openai": "OpenAI",
    }
    mode_labels = {"": "Auto", "talk": "Talk", "reason": "Reason", "code": "Code"}

    voice = "on" if getattr(session, "voice_enabled", True) else "off"
    stt   = "on" if getattr(session, "stt_enabled", True) else "off"
    grp   = "on" if getattr(session, "voice_in_groups", False) else "off"
    tts   = tts_labels.get(getattr(session, "tts_provider", "auto"), "Auto")
    mode  = mode_labels.get(getattr(session, "ai_mode", ""), "Auto")

    return (
        "⚙️ *Settings*\n\n"
        f"Voice `{voice}` · Transcribe `{stt}` · Groups `{grp}`\n"
        f"TTS: `{tts}` · Mode: `{mode}`"
    )


def build_settings_keyboard(session: Any) -> List[List[Dict[str, Any]]]:
    """Build the inline keyboard rows for /settings, reflecting current session state.

    Returns a list-of-rows suitable for ``send_message(keyboard=...)`` or wrapping
    as ``{"inline_keyboard": rows}`` for raw editMessageText calls.
    """

    def _on(active: bool) -> str:
        return " ●" if active else ""

    voice_on = getattr(session, "voice_enabled", True)
    stt_on = getattr(session, "stt_enabled", True)
    grp_on = getattr(session, "voice_in_groups", False)
    tts_p = getattr(session, "tts_provider", "auto")
    ai_mode = getattr(session, "ai_mode", "")

    return [
        # ── Voice toggles ──────────────────────────────────────
        [
            {"text": f"{'🔊' if voice_on else '🔇'} Voice  {'ON' if voice_on else 'OFF'}",  "callback_data": "st_voice"},
            {"text": f"{'🎙' if stt_on else '🚫'} Transcribe  {'ON' if stt_on else 'OFF'}", "callback_data": "st_stt"},
        ],
        [
            {"text": f"{'👥' if grp_on else '💬'} Group voice  {'ON' if grp_on else 'OFF'}", "callback_data": "st_grp"},
        ],
        # ── TTS engine ─────────────────────────────────────────
        [
            {"text": f"Auto{_on(tts_p == 'auto')}",         "callback_data": "st_tts_a"},
            {"text": f"Google ☁️{_on(tts_p == 'google_cloud')}", "callback_data": "st_tts_g"},
            {"text": f"Edge{_on(tts_p == 'edge')}",          "callback_data": "st_tts_e"},
            {"text": f"OpenAI{_on(tts_p == 'openai')}",      "callback_data": "st_tts_o"},
        ],
        # ── AI mode ────────────────────────────────────────────
        [
            {"text": f"Auto{_on(ai_mode == '')}",      "callback_data": "st_mode_a"},
            {"text": f"Talk{_on(ai_mode == 'talk')}",  "callback_data": "st_mode_t"},
            {"text": f"Reason{_on(ai_mode == 'reason')}", "callback_data": "st_mode_r"},
            {"text": f"Code{_on(ai_mode == 'code')}",  "callback_data": "st_mode_c"},
        ],
        # ── Dismiss ────────────────────────────────────────────
        [
            {"text": "✕ close", "callback_data": "st_close"},
        ],
    ]


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

    async def _answer(self, callback_id: str, text: str, show_alert: bool = False) -> None:
        await self.channel._api_call("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": show_alert,
        })

    async def _handle_approval_action(
        self,
        entry: CallbackEntry,
        user_id: Optional[int],
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

        # ── Provider-force shortcuts (ms_prov_*) ──
        prov_map = {
            "ms_prov_xai":    ("xai",    "⚡ xAI/Grok"),
            "ms_prov_openai": ("openai", "🤖 OpenAI"),
        }
        if cb_data in prov_map:
            prov_key, prov_label = prov_map[cb_data]
            self.channel._user_model_prefs[user_id] = prov_key
            await self._answer(cb_id, f"✅ Provider preference: {prov_label}")
            try:
                await self.channel._handle_models_command(chat_id, user_id)
            except Exception:
                pass
            await self.channel.send_message(
                chat_id,
                f"✅ Provider locked to *{prov_label}*\n"
                f"The system will prefer this provider for your messages.",
                parse_mode="Markdown",
            )
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
            except Exception:
                pass
            return

        if cb_data == "prov_noai":
            self.channel._user_model_prefs[user_id] = "noai"
            await self._answer(cb_id, "🚫 Raw mode — no AI on next message", show_alert=True)
            return

        if cb_data == "prov_forge":
            import socket as _sock
            forge_port = 42070
            try:
                from navig.providers.bridge_grid_reader import get_llm_port
                forge_port = get_llm_port() or 42070
            except Exception:
                pass
            try:
                sock = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
                sock.settimeout(1.0)
                online = sock.connect_ex(("127.0.0.1", forge_port)) == 0
                sock.close()
            except Exception:
                online = False
            status = f"online at port {forge_port}" if online else f"offline (expected port {forge_port})"
            await self._answer(cb_id, f"⚡ Forge Bridge: {status}", show_alert=True)
            return

        if cb_data == "prov_back":
            await self._answer(cb_id, "")
            await self.channel._handle_providers(chat_id)
            return

        # Providers that open a full model↔tier picker
        # Static map for known providers + dynamic fallback via registry
        picker_map: dict = {
            "prov_openrouter":    "openrouter",
            "prov_github":        "github_models",
            "prov_github_models": "github_models",
            "prov_nvidia":        "nvidia",
            "prov_ollama":        "ollama",
            "prov_xai":           "xai",
            "prov_openai":        "openai",
            "prov_anthropic":     "anthropic",
            "prov_google":        "google",
            "prov_groq":          "groq",
            "prov_mistral":       "mistral",
            "prov_llamacpp":      "llamacpp",
            "prov_airllm":        "airllm",
        }
        if cb_data in picker_map:
            await self._answer(cb_id, "")
            await self.channel._show_provider_model_picker(chat_id, picker_map[cb_data])
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
        prov_id = cb_data[len("prov_"):]  # strip "prov_" prefix
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
                    key_status = (
                        f"⬜ not found — set {env_hint}"
                        + (f" or vault '{vault_hint}'" if vault_hint != "—" else "")
                    )
                toast = (
                    f"{manifest.emoji} {manifest.display_name} — "
                    f"{manifest.description[:80]}{'…' if len(manifest.description) > 80 else ''} | "
                    f"Key: {key_status}"
                )
                await self._answer(cb_id, toast, show_alert=True)
                return
        except Exception:
            pass

        await self._answer(cb_id, f"Provider info unavailable for '{prov_id}'")

    async def _handle_provider_model_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle model→tier assignment buttons (pm_{prov_id}_{idx}_{tier_code}).

        Callback data format: pm_{prov_id}_{model_idx}_{tier_code}
        tier_code: s=small  b=big  c=coder_big
        prov_id may contain underscores (e.g. github_models) — split from right.
        """
        rest = cb_data[3:]  # strip "pm_"
        parts = rest.rsplit("_", 2)
        if len(parts) != 3:
            await self._answer(cb_id, "⚠️ Bad callback format")
            return
        prov_id, model_idx_str, tier_code = parts

        tier_map: dict = {
            "s": ("small",     "⚡ Small"),
            "b": ("big",       "🧠 Big"),
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

        # Resolve model list for this provider
        import json as _json
        models: list = []
        if prov_id == "ollama":
            try:
                import urllib.request
                with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                    data = _json.loads(r.read())
                    models = [m["name"] for m in data.get("models", []) if m.get("name")]
            except Exception:
                models = ["qwen2.5:7b", "qwen2.5:3b", "phi3.5", "llama3.2"]
        else:
            try:
                from navig.providers.registry import _INDEX as PROV_INDEX
                manifest = PROV_INDEX.get(prov_id)
                if manifest:
                    models = list(manifest.models)
            except Exception:
                pass

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
                    "⚠️ Hybrid router not active — enable routing in config.yaml first",
                    show_alert=True,
                )
                return
            slot = router.cfg.slot_for_tier(tier)
            slot.provider = prov_id
            slot.model = model
            await self._answer(cb_id, f"✅ {tier_label} → {model[:40]}")
            await self.channel.send_message(
                chat_id,
                f"✅ *{tier_label}* set to `{prov_id}:{model}`\n"
                f"_Active for this session. Use /model to verify._",
                parse_mode="Markdown",
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
            await self.channel._handle_providers(chat_id)
        elif cb_data == "trace_model":
            await self._answer(cb_id, "")
            await self.channel._handle_models_command(chat_id, user_id)
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
                await self.channel._api_call("deleteMessage", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                })
            except Exception:
                pass
            return

        # Map callback → (field, value) or toggle
        _TOGGLE = {
            "st_voice": "voice_enabled",
            "st_stt":   "stt_enabled",
            "st_grp":   "voice_in_groups",
        }
        _SELECT = {
            "st_tts_a": ("tts_provider", "auto"),
            "st_tts_g": ("tts_provider", "google_cloud"),
            "st_tts_e": ("tts_provider", "edge"),
            "st_tts_o": ("tts_provider", "openai"),
            "st_mode_a": ("ai_mode", ""),
            "st_mode_t": ("ai_mode", "talk"),
            "st_mode_r": ("ai_mode", "reason"),
            "st_mode_c": ("ai_mode", "code"),
        }
        _TOAST = {
            "st_voice":   lambda s: f"🔊 Voice {'ON' if s.voice_enabled else 'OFF'}",
            "st_stt":     lambda s: f"🎙 Transcription {'ON' if s.stt_enabled else 'OFF'}",
            "st_grp":     lambda s: f"👥 Group voice {'ON' if s.voice_in_groups else 'OFF'}",
            "st_tts_a":   lambda _: "⚡ TTS: Auto",
            "st_tts_g":   lambda _: "☁️ TTS: Google Cloud",
            "st_tts_e":   lambda _: "🔷 TTS: Edge",
            "st_tts_o":   lambda _: "🤖 TTS: OpenAI",
            "st_mode_a":  lambda _: "🔄 Mode: Auto",
            "st_mode_t":  lambda _: "💬 Mode: Talk",
            "st_mode_r":  lambda _: "🧠 Mode: Reason",
            "st_mode_c":  lambda _: "💻 Mode: Code",
        }

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
        except Exception:
            pass

        toast = _TOAST.get(cb_data, lambda _: "✅ Updated")(session)
        await self._answer(cb_id, toast)

        # Refresh the settings message in-place
        keyboard_rows = build_settings_keyboard(session)
        try:
            await self.channel._api_call("editMessageText", {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": _settings_header_text(session),
                "parse_mode": "Markdown",
                "reply_markup": {"inline_keyboard": keyboard_rows},
            })
        except Exception as exc:
            logger.debug("Settings refresh failed: %s", exc)

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
