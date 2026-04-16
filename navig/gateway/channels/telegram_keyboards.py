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
import html
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

from navig.gateway.channels.telegram_utils import escape_mdv2 as _mdv2_escape

if TYPE_CHECKING:
    from navig.gateway.channels.telegram import TelegramChannel

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────

MAX_BUTTONS_PER_ROW = 3
MAX_ROWS = 2
MAX_EXPAND_ROWS = 5
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
# Tier code → (tier name, display label) — shared by pms_ and mdl_sel_ handlers
_TIER_CODE_MAP: dict[str, tuple[str, str]] = {
    "s": ("small", "⚡ Small"),
    "b": ("big", "🧠 Big"),
    "c": ("coder_big", "💻 Code"),
}


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

    # Default → no buttons for short conversational replies.
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

    TTL: entries expire after ttl_seconds (default 24h).
    """

    def __init__(self, max_entries: int = 500, ttl_seconds: int = 86400):
        self._store: dict[str, dict[str, Any]] = {}
        self._max = max_entries
        self._ttl = ttl_seconds

    def put(self, key: str, entry: Any, ttl: int | None = None) -> None:
        self._expire_old()
        if len(self._store) >= self._max:
            def _created_at_value(item: tuple[str, Any]) -> float:
                value = item[1]
                if isinstance(value, dict):
                    return float(value.get("created_at", 0.0))
                return float(getattr(value, "created_at", 0.0))

            items = sorted(self._store.items(), key=_created_at_value)
            to_remove = len(items) // 5 or 1
            for k, _ in items[:to_remove]:
                del self._store[k]
        self._store[key] = {
            "value": entry,
            "created_at": time.time(),
            "ttl": int(ttl if ttl is not None else self._ttl),
        }

    def get(self, key: str) -> Any | None:
        payload = self._store.get(key)
        if payload is None:
            return None

        # Backward compatibility: legacy direct storage of CallbackEntry/Any
        if not isinstance(payload, dict):
            created_at = float(getattr(payload, "created_at", 0.0))
            if time.time() - created_at > self._ttl:
                del self._store[key]
                return None
            return payload

        # Check TTL
        created_at = float(payload.get("created_at", 0.0))
        ttl = int(payload.get("ttl", self._ttl))
        if time.time() - created_at > ttl:
            del self._store[key]
            return None
        return payload.get("value")

    def remove(self, key: str) -> None:
        self._store.pop(key, None)

    def _expire_old(self) -> None:
        """Remove entries older than TTL."""
        now = time.time()
        expired: list[str] = []
        for k, v in self._store.items():
            if isinstance(v, dict):
                created_at = float(v.get("created_at", 0.0))
                ttl = int(v.get("ttl", self._ttl))
                is_expired = now - created_at > ttl
            else:
                created_at = float(getattr(v, "created_at", 0.0))
                is_expired = now - created_at > self._ttl
            if is_expired:
                expired.append(k)
        for k in expired:
            del self._store[k]


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
        explore_questions: list[str] | None = None,
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
            rows = self._build_expand_rows(
                msg_hash, user_message, ai_response, category,
                explore_questions=list(explore_questions or []),
            )
        elif profile == KeyboardProfile.FEEDBACK:
            rows = self._build_feedback_rows(msg_hash, user_message, ai_response)

        if not rows:
            return None
        if profile == KeyboardProfile.EXPAND:
            return rows[:MAX_EXPAND_ROWS]
        return rows[:MAX_ROWS]

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
            digest = _short_hash(cb_key, length=12)
            action_max = max(1, MAX_CALLBACK_DATA - len(digest) - 1)
            cb_key = f"{action[:action_max]}:{digest}"

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
        explore_questions: list[str] | None = None,
    ) -> list[list[dict[str, str]]]:
        """
        Expand profile: context-aware row 1 + optional explore row 2.

        Row 1 adapts to content:
          CODE  → [Explain] [Copy code]
          HOWTO → [Go Deeper] [Show steps]
          Other → [Go Deeper] [Rephrase]
        Row 2 → contextual explore questions (replaces legacy 👍/👎 feedback).
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
                    "� Глубже" if is_ru else "📖 Go Deeper",
                    "dig_deeper",
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
                    "� Глубже" if is_ru else "📖 Go Deeper",
                    "dig_deeper",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
                self._make_button(
                    "🔄 Перефраз" if is_ru else "🔄 Rephrase",
                    "rephrase",
                    msg_hash,
                    user_message,
                    ai_response,
                ),
            ]

        rows: list[list[dict[str, str]]] = [row1]

        # Row 2 — contextual explore questions (replaces legacy 👍/👎 feedback).
        # Each question is stored in CallbackStore and rendered as a button that
        # routes through _handle_ask_followup_callback like a user-typed message.
        if explore_questions:
            normalized: list[str] = []
            seen: set[str] = set()
            for q in explore_questions:
                q_clean = (q or "").strip()
                if not q_clean:
                    continue
                q_key_norm = q_clean.lower()
                if q_key_norm in seen:
                    continue
                seen.add(q_key_norm)
                normalized.append(q_clean)

            if len(normalized) < _EXPLORE_Q_MIN:
                for fallback in _EXPLORE_FALLBACKS:
                    fallback_key = fallback.lower()
                    if fallback_key in seen:
                        continue
                    seen.add(fallback_key)
                    normalized.append(fallback)
                    if len(normalized) >= _EXPLORE_Q_MIN:
                        break

            for idx, q in enumerate(normalized[:_EXPLORE_Q_MAX]):
                q_key = f"eq{idx}:{msg_hash}"
                self.store.put(_FOLLOWUP_STORE_PREFIX + q_key, q)
                cb = _FOLLOWUP_CB_PREFIX + q_key
                if len(cb) <= MAX_CALLBACK_DATA:
                    rows.append([{"text": q[:MAX_BUTTON_TEXT], "callback_data": cb}])

        return rows

    def _build_feedback_rows(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
    ) -> list[list[dict[str, str]]]:
        """Legacy feedback profile — 👍/👎 removed. Returns no buttons."""
        return []


# ────────────────────────────────────────────────────────────────
# CallbackHandler
# ────────────────────────────────────────────────────────────────

# ── Dig Deeper — prompt/regex constants (no inline literals in methods) ──────
#
# The LLM is asked to append a follow-up question using a fixed marker line so
# it can be stripped from the displayed text and stored for the ↗️ button.
_DIG_DEEPER_FOLLOWUP_MARKER: str = "FOLLOW_UP_QUESTION:"
_DIG_DEEPER_PROMPT: str = (
    'The user asked: "{user_message}"\n\n'
    'Your initial answer was:\n"""\n{ai_response}\n"""\n\n'
    "Go significantly deeper. Expand with additional context, nuance, examples, "
    "edge cases, or a different angle the user may not have considered. "
    "After your expanded answer, on a NEW LINE write exactly:\n"
    f"{_DIG_DEEPER_FOLLOWUP_MARKER} <one natural follow-up question the user is likely to have>"
)
_REPHRASE_PROMPT: str = (
    'The user asked: "{user_message}"\n\n'
    'Your answer was:\n"""\n{ai_response}\n"""\n\n'
    "Rephrase the answer using a different structure, tone, or level of detail "
    "to make the information clearer or more engaging."
)
# Matches the marker + the follow-up question appended by the LLM.
_FOLLOWUP_EXTRACT_RE = re.compile(
    r"\n+" + re.escape(_DIG_DEEPER_FOLLOWUP_MARKER) + r"\s*(.+)$",
    re.DOTALL,
)
# Store key prefix for follow-up questions keyed by msg_hash.
_FOLLOWUP_STORE_PREFIX: str = "followup:"
# Label prefix shown on the inline button.
_FOLLOWUP_BTN_LABEL: str = "\u2197\ufe0f Ask this"  # ↗️ Ask this
# Action prefix embedded in callback_data for follow-up buttons.
_FOLLOWUP_CB_PREFIX: str = "ask_followup:"

# ── Explore questions (contextual follow-ups injected into REASON-mode responses)
# The LLM appends a pipe-delimited list after a fixed marker line.
# extract_explore_questions() strips it from display and returns the questions
# so they can be rendered as inline 'explore' buttons (replacing legacy feedback).
_EXPLORE_Q_RE = re.compile(
    r"\n*EXPLORE_Q:\s*([^\n]+)",
    re.IGNORECASE,
)
_EXPLORE_Q_MIN: int = 2  # always show at least this many contextual follow-ups
_EXPLORE_Q_MAX: int = 4  # max explore buttons rendered per response
_EXPLORE_FALLBACKS: tuple[str, ...] = (
    "Want the key takeaways?",
    "Need a spoiler-free version?",
    "Should I explain it simpler?",
    "Want related recommendations?",
)


def extract_explore_questions(text: str) -> tuple[str, list[str]]:
    """Strip EXPLORE_Q: marker and return (cleaned_text, [question, ...]).

    The LLM appends a line like::

        EXPLORE_Q: Who directed this? | Other Fincher films? | Full cast list?

    This function strips that line from the visible text and returns the
    questions so they can be shown as contextual explore inline buttons.
    """
    m = _EXPLORE_Q_RE.search(text)
    if not m:
        return text, []
    cleaned = text[: m.start()].rstrip()
    raw = m.group(1).strip()
    questions = [q.strip() for q in raw.split("|") if q.strip()]
    return cleaned, questions[:_EXPLORE_Q_MAX]


_ACTION_PROMPTS: dict[str, str] = {
    "regen": (
        'The user asked: "{user_message}"\n'
        "Your previous answer was not satisfactory. "
        "Provide a better, more complete answer."
    ),
    "rephrase": _REPHRASE_PROMPT,
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
    # Special handlers (no prompt template — handled in CallbackHandler directly)
    "dig_deeper": None,
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
        "🔊 <b>Audio Settings</b>\n\n"
        f"Voice input → <code>{voice_mode}</code> · Text input → <code>{text_mode}</code>\n"
        f"Groups <code>{grp}</code> · Provider <code>{tts}</code>"
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
    except Exception as exc:
        logger.debug("MOOD_REGISTRY lookup failed: %s", exc)
        focus_label = getattr(session, "focus_mode", "balance")

    tts_p = getattr(session, "tts_provider", "auto")
    vr = "on" if getattr(session, "voice_replies", False) else "off"
    ai_m = getattr(session, "ai_mode", "") or "auto"
    return (
        "⚙️ <b>NAVIG Settings</b>\n\n"
        f"Focus <code>{focus_label}</code> · AI mode <code>{ai_m}</code>\n"
        f"Voice replies <code>{vr}</code> · TTS <code>{tts_p}</code>\n\n"
        "<i>Tap a section — /voice for full audio settings</i>"
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
            {"text": "🎤  Voice API Keys", "callback_data": "st_goto_voice_provider"},
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
            # ── Single-action help button on context card ──
            if cb_data == "helpme":
                await self._answer(cb_id, "")
                handler = getattr(self.channel, "_handle_help", None)
                if handler:
                    await handler(chat_id=chat_id)
                return

            # ── Help Encyclopedia navigation (help:*) ──
            if cb_data.startswith("help:"):
                await self._answer(cb_id, "")
                handler = getattr(self.channel, "_handle_help_callback", None)
                if handler:
                    await handler(cb_data, chat_id, message_id)
                return

            if cb_data.startswith("nav:"):
                await self._handle_navigation_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Model switcher callbacks (ms_*) — no store needed ──
            if cb_data.startswith("task:"):
                await self._handle_task_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Card navigator callbacks (card:*) ──
            if cb_data.startswith("card:"):
                try:
                    from navig.gateway.channels.telegram_navigator import handle_card_callback

                    await handle_card_callback(self.channel, callback_query, self.store)
                except Exception as _nav_err:
                    logger.warning(
                        "Card navigator callback error: chat_id=%s callback=%s err=%s",
                        chat_id,
                        cb_data,
                        _nav_err,
                    )
                    await self._answer(cb_id, "⚠️ Card action error")
                return

            # ── Refiner callbacks (rfn:*) ──
            if cb_data.startswith("rfn:"):
                try:
                    from navig.gateway.channels.telegram_refiner import handle_rfn_callback

                    await handle_rfn_callback(self.channel, callback_query, self.store)
                except Exception as _rfn_err:
                    logger.warning(
                        "Refiner callback error: chat_id=%s callback=%s err=%s",
                        chat_id,
                        cb_data,
                        _rfn_err,
                    )
                    await self._answer(cb_id, "⚠️ Refine action error")
                return

            if cb_data.startswith("ms_"):
                await self._handle_model_switch(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Provider model picker callbacks (pmv_*/pms_*) — no store needed ──
            if cb_data.startswith("pmv_") or cb_data.startswith("pms_"):
                await self._handle_provider_model_callback(
                    cb_id, cb_data, chat_id, message_id, user_id
                )
                return

            # ── Provider hub callbacks (prov_*) — no store needed ──
            if cb_data.startswith("prov_"):
                await self._handle_provider_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Models flow callbacks (mdl_*) — no store needed ──
            if cb_data.startswith("mdl_"):
                await self._handle_models_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── AI tier picker callbacks (aitier_* / ai_close) — no store needed ──
            if cb_data.startswith("aitier_") or cb_data == "ai_close":
                await self._handle_ai_tier_callback(cb_id, cb_data, chat_id, message_id, user_id)
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
                    await handler(cb_id, cb_data, chat_id, user_id)
                else:
                    await self._answer(cb_id, "⚠️ Action unavailable")
                return

            # ── Setup-fix callbacks from /status (stfix:*) — no store needed ──
            if cb_data.startswith("stfix:"):
                handler = getattr(self.channel, "_handle_status_fix_callback", None)
                if handler:
                    await handler(cb_id, cb_data, chat_id, user_id)
                else:
                    await self._answer(cb_id, "⚠️ Setup fix unavailable")
                return

            # ── Hybrid routing callbacks (hyb_*) — provider control surface ──
            if cb_data.startswith("hyb_"):
                await self._handle_hybrid_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Vision picker callbacks (vis_*) — provider control surface ──
            if cb_data.startswith("vis_"):
                await self._handle_vision_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Provider utility callbacks (pu_*) — provider control surface ──
            if cb_data.startswith("pu_"):
                await self._handle_provider_utility_callback(
                    cb_id, cb_data, chat_id, message_id, user_id
                )
                return

            # ── Audio deep menu (audio:*) — no store needed ──
            if cb_data.startswith("audio:"):
                try:
                    from navig.gateway.channels.audio_menu import handle_audio_callback

                    await handle_audio_callback(
                        self.channel, cb_id, cb_data, chat_id, message_id, user_id
                    )
                except Exception as _amc_err:
                    logger.warning(
                        "Audio menu callback error: chat_id=%s callback=%s err=%s",
                        chat_id,
                        cb_data,
                        _amc_err,
                    )
                    await self._answer(cb_id, "\u26a0\ufe0f Audio menu error")
                return

            # ── Audio file action buttons (audmsg:*) — no store needed ──
            if cb_data.startswith("audmsg:"):
                await self._handle_audio_file_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Evening summary action buttons (eve:*) — no store needed ──
            if cb_data.startswith("eve:"):
                await self._handle_evening_callback(cb_id, cb_data, chat_id, message_id, user_id)
                return

            # ── Ask this (follow-up question routed as new user message) ──
            if cb_data.startswith(_FOLLOWUP_CB_PREFIX):
                await self._handle_ask_followup_callback(
                    cb_id=cb_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    followup_key=_FOLLOWUP_STORE_PREFIX + cb_data[len(_FOLLOWUP_CB_PREFIX):],
                )
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
                    code_text = "\n\n".join(block.strip("`").strip() for block in code_blocks)
                    await self._answer(cb_id, "📋 Code extracted")
                    await self.channel.send_message(chat_id, f"<pre>{html.escape(code_text[:3900])}</pre>", parse_mode="HTML")
                else:
                    await self._answer(cb_id, "No code blocks found")
                return

            # ── Go Deeper — AI expansion + follow-up question button ──
            if action == "dig_deeper":
                await self._handle_dig_deeper_callback(
                    cb_id=cb_id,
                    chat_id=chat_id,
                    user_id=user_id,
                    cb_key=cb_data,
                    entry=entry,
                )
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
                            await self.channel.send_message(chat_id, response, keyboard=keyboard)
                        else:
                            await self.channel.send_message(
                                chat_id, "❌ Couldn't generate a response."
                            )
                    finally:
                        typing_task.cancel()
                        try:
                            await typing_task
                        except asyncio.CancelledError as exc:
                            logger.debug("Exception suppressed (typing task cancelled): %s", exc)
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
            logger.exception(
                "Callback handling failed: chat_id=%s callback=%s err=%s", chat_id, cb_data, exc
            )
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

    async def _show_callback_error_screen(
        self, chat_id: int, message_id: int, cb_data: str
    ) -> None:
        if not message_id:
            await self.channel.send_message(
                chat_id,
                "⚠️ Something went wrong. Use /start to return home.",
                parse_mode=None,
            )
            return
        await self.channel._api_call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": "⚠️ Something went wrong. You can safely return home.",
                "reply_markup": {
                    "inline_keyboard": [
                        [{"text": "🏠 Home", "callback_data": "nav:home"}],
                    ]
                },
            },
        )

    async def _handle_task_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle task:* callbacks safely, delegating when channel-level handler exists."""
        handler = getattr(self.channel, "_handle_task_callback", None)
        if handler:
            await handler(cb_id, cb_data, chat_id, message_id, user_id)
            return

        task_views = getattr(self.channel, "task_views", None)
        if not isinstance(task_views, dict) or not message_id:
            await self._answer(cb_id, "⏳ Task card expired")
            return

        view = task_views.get(message_id)
        if view is None:
            await self._answer(cb_id, "⏳ Task card expired")
            return

        action = cb_data.split(":", 1)[1] if ":" in cb_data else ""

        from navig.gateway.channels.task_card import StepState, update_task_card

        if action == "toggle_details":
            view.expanded = not bool(getattr(view, "expanded", True))
            await update_task_card(self.channel, chat_id, view, force=True)
            await self._answer(cb_id, "")
            return

        if action == "toggle_pause":
            if bool(getattr(view, "done", False)):
                await self._answer(cb_id, "✅ Task already completed")
                return

            view.running = not bool(getattr(view, "running", True))
            active = getattr(view, "active_step", None)
            if active is not None:
                if view.running and getattr(active, "state", None) == StepState.PAUSED:
                    active.state = StepState.ACTIVE
                elif (not view.running) and getattr(active, "state", None) == StepState.ACTIVE:
                    active.state = StepState.PAUSED

            view.recompute_percent()
            await update_task_card(self.channel, chat_id, view, force=True)
            await self._answer(cb_id, "▶️ Resumed" if view.running else "⏸️ Paused")
            return

        if action == "stop":
            if bool(getattr(view, "done", False)):
                await self._answer(cb_id, "✅ Task already completed")
                return

            view.running = False
            view.done = True
            active = getattr(view, "active_step", None)
            if active is not None and getattr(active, "state", None) == StepState.ACTIVE:
                active.state = StepState.FAILED
                active.detail = "Stopped by user"
            view.recompute_percent()
            await update_task_card(self.channel, chat_id, view, force=True)
            await self._answer(cb_id, "🛑 Stopped")
            return

        if action == "refresh":
            await update_task_card(self.channel, chat_id, view, force=True)
            await self._answer(cb_id, "🔄 Refreshed")
            return

        await self._answer(cb_id, "⚠️ Unknown task action")

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
            # Main menu has been replaced by the context card — delegate to /start.
            start_handler = getattr(self.channel, "_handle_start", None)
            if start_handler:
                await start_handler(chat_id=chat_id, username="", user_id=user_id)
            return

        if action == "providers":
            await self._answer(cb_id, "")
            await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            return

        if action == "models":
            await self._answer(cb_id, "")
            await self.channel._handle_models_command(chat_id, user_id, message_id=message_id)
            return

        await self._answer(cb_id, "⚠️ Unknown navigation")

    async def _answer(self, callback_id: str, text: str, show_alert: bool = False) -> None:
        if not callback_id:
            return
        if callback_id in self._answered_callback_ids:
            return
        result = await self.channel._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert,
            },
        )
        if result is not None:
            self._answered_callback_ids.add(callback_id)

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

            await self._answer(cb_id, f"✅ Switched to {label}")

            # Update the original message with refreshed keyboard
            try:
                await self.channel._handle_models_command(
                    chat_id,
                    user_id,
                    message_id=message_id,
                )
            except Exception as _mre:  # noqa: BLE001
                logger.debug("Model tier keyboard refresh failed: %s", _mre)
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
                        f"<pre>{html.escape(table)}</pre>",
                        parse_mode="HTML",
                    )
                else:
                    await self._answer(cb_id, "Routing not active")
            except Exception as exc:
                logger.warning("ms_info routing table failed: %s", exc)
                await self._answer(cb_id, "\u26a0\ufe0f Routing info unavailable")
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
            await self.channel._show_provider_model_picker(chat_id, prov_id, message_id=message_id)
            return

        await self._answer(cb_id, "⚠️ Unknown model action")

    async def _handle_ai_tier_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle /ai tier-picker inline buttons (aitier_* / ai_close callbacks)."""
        if cb_data == "ai_close":
            await self._answer(cb_id, "✖ Closed")
            try:
                await self.channel._api_call(
                    "deleteMessage",
                    {"chat_id": chat_id, "message_id": message_id},
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Exception suppressed: %s", exc
                )  # best-effort; failure is non-critical
            return

        # aitier_{key} — key is "auto", "small", "big", or "coder_big"
        tier_raw = cb_data[len("aitier_") :]
        tier_key = "" if tier_raw == "auto" else tier_raw

        if hasattr(self.channel, "_set_user_tier_pref"):
            self.channel._set_user_tier_pref(chat_id, user_id, tier_key)
        else:
            # Fallback: write directly to _user_model_prefs dict
            prefs = getattr(self.channel, "_user_model_prefs", None)
            if prefs is not None:
                if tier_key:
                    prefs[user_id] = tier_key
                else:
                    prefs.pop(user_id, None)

        # Re-render the /ai panel in-place to reflect the selection
        handler = getattr(self.channel, "_handle_ai_command", None)
        if handler:
            try:
                await handler(chat_id=chat_id, user_id=user_id, message_id=message_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Exception suppressed: %s", exc
                )  # best-effort; if edit fails, still ack the tap

        tier_labels = {
            "": "🔄 Auto",
            "small": "⚡ Small",
            "big": "🧠 Big",
            "coder_big": "💻 Coder",
        }
        label = tier_labels.get(tier_key, tier_key)
        await self._answer(cb_id, f"{label} tier selected")

    async def _activate_provider_with_defaults(
        self,
        cb_id: str,
        chat_id: int,
        message_id: int,
        prov_id: str,
        manifest: Any,
    ) -> bool:
        """Resolve models, assign curated tier defaults, update routers, navigate to tier summary.

        Shared by prov_activate_, prov_{id}, and mdl_prov_ callback handlers.
        Provider selection is always persisted immediately regardless of model availability.
        Returns True on success.
        """
        models: list = []
        if hasattr(self.channel, "_resolve_provider_models"):
            try:
                models = await self.channel._resolve_provider_models(prov_id, manifest=manifest)
            except Exception:
                logger.warning("Provider model resolution failed for pms_%s", prov_id)
                models = list(getattr(manifest, "models", []) or [])

        if not models and manifest and getattr(manifest, "tier", "") == "local":
            if prov_id == "llamacpp":
                models = ["llama.cpp/default", "llama3.2"]
            elif prov_id == "ollama":
                models = ["qwen2.5:7b", "phi3.5"]

        defaults = self.channel._select_curated_tier_defaults(prov_id, models)

        # Update hybrid router (best-effort — write regardless of is_active so slots
        # are pre-populated and the tier-summary display can read them back)
        try:
            from navig.agent.ai_client import get_ai_client

            router = get_ai_client().model_router
            if router:
                for tier in ("small", "big", "coder_big"):
                    slot = router.cfg.slot_for_tier(tier)
                    slot.provider = prov_id
                    if defaults.get(tier):
                        slot.model = defaults[tier]
                if hasattr(self.channel, "_persist_hybrid_router_assignments"):
                    self.channel._persist_hybrid_router_assignments(router.cfg)
        except Exception:  # noqa: BLE001
            logger.debug("Hybrid router update skipped for provider=%s", prov_id)

        # Always update LLM Mode Router (primary routing layer) — persists provider immediately
        if hasattr(self.channel, "_update_llm_mode_router"):
            self.channel._update_llm_mode_router(prov_id, defaults)

        prov_emoji = manifest.emoji if manifest else ""
        prov_name = manifest.display_name if manifest else prov_id
        await self._answer(cb_id, f"\u2705 {prov_emoji} {prov_name} activated")

        try:
            from navig.commands.init import mark_chat_onboarding_step_completed

            mark_chat_onboarding_step_completed("ai-provider")
        except (ImportError, AttributeError, TypeError, ValueError):
            logger.debug("Unable to mark ai-provider onboarding step for %s", prov_id)

        if not models:
            # Provider is saved; no models resolved yet.
            # Distinguish between "no key" (most common) and "key OK but no models".
            _has_api_key = False
            try:
                from navig.agent.model_router import _resolve_provider_api_key
                from navig.config import get_config_manager as _gcm

                _has_api_key = bool(
                    _resolve_provider_api_key(prov_id, _gcm().global_config or {})
                )
            except Exception:
                pass
            _safe_prov_name = html.escape(prov_name)
            if _has_api_key:
                _warn_msg = (
                    f"⚠️ No models found for {_safe_prov_name} — use /models to assign models manually."
                )
            else:
                _warn_msg = (
                    f"🔑 <b>API key required for {_safe_prov_name}.</b>\n"
                    "Go back to the provider list and tap ⚙️ Configure to enter your key.\n"
                    "Once the key is saved, select the provider again to activate it."
                )
            await self.channel.send_message(chat_id, _warn_msg, parse_mode="HTML")
            return True

        await self.channel._show_models_tier_summary(
            chat_id,
            prov_id,
            message_id=message_id,
        )
        return True

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
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Exception suppressed: %s", exc
                )  # best-effort; failure is non-critical
            return

        if cb_data == "prov_noai":
            if hasattr(self.channel, "_set_one_shot_noai"):
                self.channel._set_one_shot_noai(user_id)
            else:
                self.channel._user_model_prefs[user_id] = "noai"
            await self._answer(cb_id, "🚫 Raw mode armed for next message")
            await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            return

        if cb_data == "prov_bridge":
            online, url = await self.channel._probe_bridge_grid()
            if online:
                await self._answer(cb_id, "⚡ Bridge active — browsing model tiers")
                await self.channel._show_models_tier_summary(
                    chat_id, "bridge_copilot", message_id=message_id
                )
            else:
                await self._answer(cb_id, f"⚡ Bridge is offline ({url}).", show_alert=True)
            return

        if cb_data == "prov_bridge_offline":
            await self._answer(
                cb_id,
                "⚡ Bridge is offline.",
                show_alert=True,
            )
            return

        if cb_data == "prov_noop":
            await self._answer(cb_id, "")
            return

        if cb_data == "prov_back":
            await self._answer(cb_id, "")
            await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            return

        # ── Pagination: prov_page_{prov_id}_{tier_code}_{page} ──────────────
        if cb_data.startswith("prov_page_"):
            rest = cb_data[len("prov_page_") :]
            # format: {prov_id}_{tier_code}_{page}  (prov_id may contain underscores)
            parts = rest.rsplit("_", 2)
            if len(parts) == 3:
                prov_id_p, tier_code, page_str = parts
                try:
                    page_num = int(page_str)
                except ValueError:
                    page_num = 0
                await self._answer(cb_id, "")
                await self.channel._show_provider_model_picker(
                    chat_id,
                    prov_id_p,
                    page=page_num,
                    selected_tier=tier_code,
                    message_id=message_id,
                    show_models=True,
                )
            else:
                await self._answer(cb_id, "⚠️ Bad page callback")
            return

        # ── Activate provider: prov_activate_{prov_id} ──────────────────────
        if cb_data.startswith("prov_activate_"):
            prov_id_a = cb_data[len("prov_activate_") :]
            try:
                from navig.providers.registry import get_provider

                manifest = get_provider(prov_id_a)
                await self._activate_provider_with_defaults(
                    cb_id, chat_id, message_id, prov_id_a, manifest
                )
            except Exception as exc:
                logger.warning("Provider activation failed (prov_activate_): %s", exc)
                await self._answer(cb_id, "⚠️ Activation failed — please try again", show_alert=True)
            return

        # ── Deactivate provider: prov_deactivate_{prov_id} ──────────────────
        if cb_data.startswith("prov_deactivate_"):
            prov_id_d = cb_data[len("prov_deactivate_") :]
            try:
                from navig.providers.registry import get_provider as _gp

                _manifest_d = _gp(prov_id_d)
                _dname = (
                    getattr(_manifest_d, "display_name", None) or prov_id_d
                ) if _manifest_d else prov_id_d
                _demoji = getattr(_manifest_d, "emoji", "") if _manifest_d else ""
            except Exception:
                _dname = prov_id_d
                _demoji = ""
            if hasattr(self.channel, "_deactivate_provider"):
                try:
                    self.channel._deactivate_provider(prov_id_d)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Provider deactivation failed for %s: %s", prov_id_d, exc)
            # Arm session-level raw mode so the very next message doesn't hit a stale provider
            if hasattr(self.channel, "_set_one_shot_noai"):
                self.channel._set_one_shot_noai(user_id)
            else:
                self.channel._user_model_prefs[user_id] = "noai"
            await self._answer(
                cb_id,
                f"🔴 {_demoji} {_dname} deactivated — raw mode active".strip(),
            )
            # Refresh the picker in-place so the Activate button appears again
            try:
                await self.channel._show_provider_model_picker(
                    chat_id,
                    prov_id_d,
                    page=0,
                    selected_tier="s",
                    message_id=message_id,
                    show_models=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Picker refresh after deactivation failed for %s: %s", prov_id_d, exc)
            return

        # Providers that open a full model↔tier picker
        # Static map for known providers + dynamic fallback via registry
        picker_map: dict = {
            "prov_openrouter": "openrouter",
            "prov_github": "github_models",
            "prov_github_models": "github_models",
            "prov_nvidia": "nvidia",
            "prov_nvidia_nim": "nvidia",
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
            # Answer the callback immediately so Telegram removes the loading
            # spinner regardless of how long the picker takes to render.
            await self._answer(cb_id, "")
            # Push "providers" onto the nav stack so Back returns to providers list.
            _nav = self.channel._get_navigation_state(chat_id)
            _stk = _nav.setdefault("screen_stack", ["main"])
            if not _stk or _stk[-1] != "providers":
                _stk.append("providers")
            try:
                await self.channel._show_provider_model_picker(
                    chat_id,
                    prov_id,
                    page=0,
                    selected_tier="s",
                    message_id=message_id,
                )
            except TypeError as exc:
                err = str(exc)
                signature_mismatch = (
                    "unexpected keyword argument" in err
                    or "positional argument" in err
                    or "required positional argument" in err
                )
                if signature_mismatch:
                    await self.channel._show_provider_model_picker(chat_id, prov_id=prov_id)
                else:
                    logger.warning(
                        "Provider picker failed for %s: %s",
                        prov_id,
                        exc,
                    )
                    try:
                        await self.channel._show_provider_model_picker(chat_id, prov_id)
                    except Exception:
                        # Callback already answered; fall back to providers hub silently.
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
                try:
                    await self.channel._show_provider_model_picker(chat_id, prov_id)
                except Exception:
                    # Callback already answered; fall back to providers hub silently.
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

        # ── Customize provider: prov_customize_{prov_id} → open /models ─────
        if cb_data.startswith("prov_customize_"):
            cust_prov_id = cb_data[len("prov_customize_") :]
            await self._answer(cb_id, "")
            try:
                await self.channel._show_models_tier_summary(
                    chat_id,
                    cust_prov_id,
                    message_id=message_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Customize models failed for %s: %s", cust_prov_id, exc)
                await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            return

        # ── Config: prov_cfg_{prov_id} → prompt user to enter API key ─────────
        if cb_data.startswith("prov_cfg_"):
            cfg_prov_id = cb_data[len("prov_cfg_") :]
            await self._answer(cb_id, "")
            try:
                result = await self.channel._api_call(
                    "sendMessage",
                    {
                        "chat_id": chat_id,
                        "text": (
                            f"🔑 <b>Enter your {cfg_prov_id} API key:</b>\n\n"
                            "Paste it in a reply — it will be stored securely in your vault.\n"
                            "<i>Send</i> <code>cancel</code> <i>to abort.</i>"
                        ),
                        "parse_mode": "HTML",
                        "reply_markup": {"force_reply": True, "selective": True},
                    },
                )
                prompt_msg_id = (result or {}).get("result", {}).get("message_id")
            except Exception as exc:
                logger.warning("Failed to send API key prompt for %s: %s", cfg_prov_id, exc)
                prompt_msg_id = None
            if not hasattr(self.channel, "_pending_api_key_input"):
                self.channel._pending_api_key_input = {}
            self.channel._pending_api_key_input[user_id] = {
                "provider": cfg_prov_id,
                "chat_id": chat_id,
                "prompt_msg_id": prompt_msg_id,
            }
            return

        # ── Dynamic provider: prov_{id} → immediate activation ──────────────
        _PROV_ALIASES: dict[str, str] = {
            "github": "github_models",
            "nvidia_nim": "nvidia",
        }
        prov_id = cb_data[len("prov_") :]
        prov_id = _PROV_ALIASES.get(prov_id, prov_id)

        try:
            from navig.providers.registry import get_provider

            manifest = get_provider(prov_id)
        except Exception:  # noqa: BLE001
            manifest = None

        if not manifest:
            await self._answer(cb_id, f"Provider info unavailable for '{prov_id}'")
            return

        # Activate with curated defaults via shared helper
        try:
            await self._activate_provider_with_defaults(
                cb_id, chat_id, message_id, prov_id, manifest
            )
        except Exception as exc:
            logger.warning("Provider activation failed (prov_*): %s", exc)
            await self._answer(cb_id, "⚠️ Activation failed — please try again", show_alert=True)

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
        - `pms_{prov_id}_{model_idx}_{tier_code}_{page}`: assign selected model to tier
        """
        if cb_data.startswith("pmv_"):
            rest = cb_data[4:]
            parts = rest.rsplit("_", 2)
            if len(parts) != 3:
                await self._answer(cb_id, "⚠️ Bad tier switch callback")
                return
            prov_id, tier_code, page_str = parts
            if tier_code not in _TIER_CODE_MAP:
                await self._answer(cb_id, "⚠️ Unknown tier code")
                return
            try:
                page = int(page_str)
            except ValueError:
                page = 0
            await self._answer(cb_id, "")
            try:
                await self.channel._show_provider_model_picker(
                    chat_id,
                    prov_id,
                    page=page,
                    selected_tier=tier_code,
                    message_id=message_id,
                    show_models=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Provider tier tab render failed for %s/%s: %s",
                    prov_id,
                    tier_code,
                    exc,
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

        if tier_code not in _TIER_CODE_MAP:
            await self._answer(cb_id, "⚠️ Unknown tier code")
            return
        tier, tier_label = _TIER_CODE_MAP[tier_code]

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
            try:
                models = await self.channel._resolve_provider_models(prov_id, manifest=manifest)
            except Exception:
                logger.warning("Provider model resolution failed for pms_%s", prov_id)
                await self._answer(
                    cb_id, "⚠️ Could not load models for this provider", show_alert=True
                )
                return

        if model_idx < 0 or model_idx >= len(models):
            await self._answer(cb_id, "⚠️ Model index out of range")
            return
        model = models[model_idx]

        # --- Update hybrid router (optional) ---
        try:
            from navig.agent.ai_client import get_ai_client

            router = get_ai_client().model_router
            if router and router.is_active:
                slot = router.cfg.slot_for_tier(tier)
                slot.provider = prov_id
                slot.model = model
                if hasattr(self.channel, "_persist_hybrid_router_assignments"):
                    self.channel._persist_hybrid_router_assignments(router.cfg)
        except Exception:
            logger.debug("Hybrid router update skipped for pms_ assignment")

        # --- Always update primary LLM Mode Router ---
        mode_router_saved = True
        try:
            if hasattr(self.channel, "_update_llm_mode_router"):
                self.channel._update_llm_mode_router(prov_id, {tier: model})
        except Exception:  # noqa: BLE001
            mode_router_saved = False
            logger.warning("LLM mode router update failed for pms_ assignment; model not saved")
        if mode_router_saved:
            await self._answer(cb_id, f"✅ {tier_label} → {model[:40]}")
        else:
            await self._answer(cb_id, "⚠️ Model selection could not be saved", show_alert=True)
        try:
            from navig.commands.init import mark_chat_onboarding_step_completed

            mark_chat_onboarding_step_completed("ai-provider")
        except (ImportError, AttributeError, TypeError, ValueError):
            logger.debug("Unable to mark ai-provider step after tier assignment")
        try:
            await self.channel._show_provider_model_picker(
                chat_id,
                prov_id,
                page=page,
                selected_tier=tier_code,
                message_id=message_id,
                show_models=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Provider picker refresh skipped after successful pms_: %s", exc)

    # ── Provider control surface callback handlers ───────────────────────

    async def _handle_hybrid_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle hybrid routing callbacks (hyb_* prefix).

        Callback formats:
        - hyb_tier_{tier}          : show provider picker for a specific tier
        - hyb_pick_{prov_id}       : quick-pick provider (applies to last selected tier)
        - hyb_assign_{tier}_{prov} : assign provider to tier as session override
        - hyb_save                 : save session overrides to durable config
        - hyb_reset                : clear session tier overrides
        - hyb_enable               : enable hybrid routing in-process + config
        """
        if cb_data == "hyb_enable":
            await self._answer(cb_id, "")
            try:
                from navig.config import get_config_manager

                cfg_mgr = get_config_manager()
                cfg_mgr.update_global_config({"routing": {"enabled": True}})
                # Best-effort in-process activation without requiring a daemon restart
                if hasattr(self.channel, "_refresh_ai_runtime_after_router_update"):
                    self.channel._refresh_ai_runtime_after_router_update()
            except Exception as exc:  # noqa: BLE001
                logger.warning("hyb_enable failed: %s", exc)
                await self.channel.send_message(
                    chat_id, "⚠️ Failed to enable hybrid routing.", parse_mode=None
                )
                return
            # Re-render hybrid screen so it reflects the new active state
            await self.channel._handle_provider_hybrid(chat_id, user_id, message_id=message_id)
            return

        if cb_data == "hyb_save":
            # Save current session tier overrides to config
            await self._answer(cb_id, "")
            try:
                from navig.gateway.channels.telegram_sessions import get_session_manager

                sm = get_session_manager()
                overrides = sm.get_all_session_overrides(chat_id, user_id)

                saved_any = False
                tier_models: dict[str, str] = {}
                tier_providers: dict[str, str] = {}
                for tier in ("small", "big", "coder_big"):
                    prov = overrides.get(f"tier_{tier}_provider", "")
                    model = overrides.get(f"tier_{tier}_model", "")
                    if prov and model:
                        tier_models[tier] = model
                        tier_providers[tier] = prov
                        saved_any = True

                if saved_any and tier_providers:
                    if hasattr(self.channel, "_update_llm_mode_router"):
                        # Group tiers by provider so each provider's modes are updated together.
                        by_provider: dict[str, dict[str, str]] = {}
                        for tier, prov in tier_providers.items():
                            by_provider.setdefault(prov, {})[tier] = tier_models[tier]
                        for prov, tiers in by_provider.items():
                            self.channel._update_llm_mode_router(prov, tiers)
                    # Also update hybrid router slots with per-tier providers.
                    try:
                        from navig.agent.ai_client import get_ai_client

                        router = get_ai_client().model_router
                        if router and router.is_active:
                            for tier, model in tier_models.items():
                                slot = router.cfg.slot_for_tier(tier)
                                slot.provider = tier_providers[tier]
                                slot.model = model
                            if hasattr(self.channel, "_persist_hybrid_router_assignments"):
                                self.channel._persist_hybrid_router_assignments(router.cfg)
                    except Exception:  # noqa: BLE001
                        pass

                    # Clear the session overrides after saving
                    sm.clear_session_overrides(chat_id, user_id)

                    await self.channel.send_message(
                        chat_id,
                        "✅ Session overrides saved to config and cleared.",
                        parse_mode=None,
                    )
                else:
                    await self.channel.send_message(
                        chat_id,
                        "ℹ️ No tier overrides in session to save.",
                        parse_mode=None,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("hyb_save failed: %s", exc)
                await self.channel.send_message(
                    chat_id, "⚠️ Failed to save overrides.", parse_mode=None
                )
            return

        if cb_data == "hyb_reset":
            await self._answer(cb_id, "")
            await self.channel._handle_provider_reset(chat_id, user_id, message_id=message_id)
            return

        # hyb_tier_{tier} — show providers for this tier
        if cb_data.startswith("hyb_tier_"):
            tier = cb_data[len("hyb_tier_") :]
            if tier not in ("small", "big", "coder_big"):
                await self._answer(cb_id, "⚠️ Unknown tier")
                return
            await self._answer(cb_id, "")
            # Show a provider list scoped to this tier
            await self._show_hybrid_tier_picker(chat_id, message_id, user_id, tier)
            return

        # hyb_pick_{prov_id} — quick-pick provider (needs tier context)
        if cb_data.startswith("hyb_pick_"):
            prov_id = cb_data[len("hyb_pick_") :]
            await self._answer(cb_id, "")
            # Show tier assignment buttons for this provider
            await self._show_hybrid_provider_tiers(chat_id, message_id, user_id, prov_id)
            return

        # hyb_assign_{tier}_{prov_id} — assign provider to specific tier (session)
        if cb_data.startswith("hyb_assign_"):
            rest = cb_data[len("hyb_assign_") :]
            # tier_provid: tier is small/big/coder_big, rest is provider id
            for tier in ("coder_big", "big", "small"):  # longest first
                if rest.startswith(f"{tier}_"):
                    prov_id = rest[len(f"{tier}_") :]
                    break
            else:
                await self._answer(cb_id, "⚠️ Bad assignment callback")
                return

            # Resolve best model for this provider+tier
            try:
                from navig.providers.registry import get_provider

                manifest = get_provider(prov_id)
                models = await self.channel._resolve_provider_models(prov_id, manifest=manifest)
                defaults = self.channel._select_curated_tier_defaults(prov_id, models)
                model = defaults.get(tier, models[0] if models else "")
            except Exception:  # noqa: BLE001
                model = ""

            if not model:
                await self._answer(cb_id, "⚠️ No model resolved", show_alert=True)
                return

            # Set as session override
            try:
                from navig.gateway.channels.telegram_sessions import get_session_manager

                sm = get_session_manager()
                sm.set_session_override(chat_id, user_id, f"tier_{tier}_provider", prov_id)
                sm.set_session_override(chat_id, user_id, f"tier_{tier}_model", model)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to set session override for hyb_assign: %s", exc)

            short = model.split("/")[-1].split(":")[-1]
            tier_emoji = {"small": "⚡", "big": "🧠", "coder_big": "💻"}
            await self._answer(cb_id, f"{tier_emoji.get(tier, '📝')} {tier} → {short} (session)")

            # Refresh hybrid view
            await self.channel._handle_provider_hybrid(chat_id, user_id, message_id=message_id)
            return

        await self._answer(cb_id, "⚠️ Unknown hybrid action")

    async def _show_hybrid_tier_picker(
        self,
        chat_id: int,
        message_id: int,
        user_id: int,
        tier: str,
    ) -> None:
        """Show connected providers to assign to a specific tier."""
        try:
            from navig.providers.discovery import list_connected_providers
        except Exception:  # noqa: BLE001
            return

        providers = list_connected_providers()
        connected = [p for p in providers if p.connected]

        tier_emoji = {"small": "⚡", "big": "🧠", "coder_big": "💻"}
        tier_label = {"small": "Small", "big": "Big", "coder_big": "Code"}

        lines = [
            f"<b>{tier_emoji.get(tier, '📝')} Pick provider for {tier_label.get(tier, tier)}</b>",
            "",
            "<i>This sets a session override (not saved to config).</i>",
        ]

        keyboard: list[list[dict[str, str]]] = []
        for p in connected:
            keyboard.append(
                [
                    {
                        "text": f"{p.emoji} {p.display_name}",
                        "callback_data": f"hyb_assign_{tier}_{p.id}",
                    }
                ]
            )
        keyboard.append(
            [
                {"text": "🔙 Back", "callback_data": "pu_hybrid"},
                {"text": "✖ Close", "callback_data": "prov_close"},
            ]
        )

        text_payload = "\n".join(lines)
        try:
            await self.channel.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            )
        except Exception:  # noqa: BLE001
            await self.channel.send_message(
                chat_id, text_payload, parse_mode="HTML", keyboard=keyboard
            )

    async def _show_hybrid_provider_tiers(
        self,
        chat_id: int,
        message_id: int,
        user_id: int,
        prov_id: str,
    ) -> None:
        """Show tier assignment buttons for a specific provider."""
        emoji, name = "🤖", prov_id
        try:
            from navig.providers.registry import get_provider

            manifest = get_provider(prov_id)
            if manifest:
                emoji = manifest.emoji
                name = manifest.display_name
        except Exception:  # noqa: BLE001
            pass

        lines = [
            f"<b>{emoji} {html.escape(name)}</b> — assign to tier",
            "",
            "<i>Tap a tier to assign this provider (session override).</i>",
        ]

        keyboard: list[list[dict[str, str]]] = [
            [
                {"text": "⚡ Small", "callback_data": f"hyb_assign_small_{prov_id}"},
                {"text": "🧠 Big", "callback_data": f"hyb_assign_big_{prov_id}"},
                {"text": "💻 Code", "callback_data": f"hyb_assign_coder_big_{prov_id}"},
            ],
            [
                {"text": "🔙 Back", "callback_data": "pu_hybrid"},
                {"text": "✖ Close", "callback_data": "prov_close"},
            ],
        ]

        text_payload = "\n".join(lines)
        try:
            await self.channel.edit_message(
                chat_id, message_id, text_payload, parse_mode="HTML", keyboard=keyboard
            )
        except Exception:  # noqa: BLE001
            await self.channel.send_message(
                chat_id, text_payload, parse_mode="HTML", keyboard=keyboard
            )

    async def _handle_vision_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle vision picker callbacks (vis_* prefix).

        Callback formats:
        - vis_{prov_id}:{model_name} : set vision model as session override
        - vis_clear                   : clear vision session override
        """
        if cb_data == "vis_clear":
            try:
                from navig.gateway.channels.telegram_sessions import get_session_manager

                sm = get_session_manager()
                sm.set_session_override(chat_id, user_id, "vision_provider", "")
                sm.set_session_override(chat_id, user_id, "vision_model", "")
            except Exception:  # noqa: BLE001
                pass
            await self._answer(cb_id, "✅ Vision override cleared")
            # Refresh vision picker
            await self.channel._handle_provider_vision(chat_id, user_id, message_id=message_id)
            return

        # vis_{prov_id}:{model_name}
        rest = cb_data[4:]  # strip "vis_"
        if ":" not in rest:
            await self._answer(cb_id, "⚠️ Invalid vision callback")
            return

        prov_id, model_name = rest.split(":", 1)

        # Model name may be truncated by callback_data 64-byte limit.
        # If truncated, try to find the full model name from the provider.
        if model_name:
            try:
                from navig.providers.registry import get_provider

                manifest = get_provider(prov_id)
                if manifest:
                    for m in manifest.models:
                        if m == model_name or m.startswith(model_name):
                            model_name = m
                            break
            except Exception:  # noqa: BLE001
                pass

        try:
            from navig.gateway.channels.telegram_sessions import get_session_manager

            sm = get_session_manager()
            sm.set_session_override(chat_id, user_id, "vision_provider", prov_id)
            sm.set_session_override(chat_id, user_id, "vision_model", model_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to set vision session override: %s", exc)
            await self._answer(cb_id, "⚠️ Could not save override", show_alert=True)
            return

        short = model_name.split("/")[-1].split(":")[-1]
        await self._answer(cb_id, f"👁 Vision → {short} (session)")
        # Refresh vision picker
        await self.channel._handle_provider_vision(chat_id, user_id, message_id=message_id)

    async def _handle_provider_utility_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle utility button callbacks from provider hub (pu_* prefix).

        Callback formats:
        - pu_hybrid         : open hybrid routing view
        - pu_vision         : open vision picker
        - pu_show           : open routing state view
        - pu_reset_session  : reset all session overrides
        """
        if cb_data == "pu_hybrid":
            await self._answer(cb_id, "")
            await self.channel._handle_provider_hybrid(chat_id, user_id, message_id=message_id)
            return

        if cb_data == "pu_vision":
            await self._answer(cb_id, "")
            await self.channel._handle_provider_vision(chat_id, user_id, message_id=message_id)
            return

        if cb_data == "pu_show":
            await self._answer(cb_id, "")
            await self.channel._handle_provider_show(chat_id, user_id, message_id=message_id)
            return

        if cb_data == "pu_reset_session":
            await self._answer(cb_id, "")
            await self.channel._handle_provider_reset(chat_id, user_id, message_id=message_id)
            return

        if cb_data == "pu_voice":
            await self._answer(cb_id, "")
            await self.channel._handle_voice_menu(chat_id, user_id, message_id=message_id)
            return

        await self._answer(cb_id, "⚠️ Unknown action", show_alert=True)

    # ── End provider control surface callbacks ───────────────────────────

    async def _handle_models_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle /models interactive flow callbacks (mdl_* namespace).

        Callback formats:
        - mdl_close                         : delete message
        - mdl_chgprov                       : navigate to /providers
        - mdl_prov_{prov_id}                : activate provider + show tier summary
        - mdl_tier_{prov_id}_{tier_code}    : show model list for tier
        - mdl_sel_{prov_id}_{idx}_{tc}_{pg} : assign model to tier
        - mdl_page_{prov_id}_{tc}_{page}    : paginate model list
        - mdl_back_tiers_{prov_id}          : back to tier summary
        """
        if cb_data == "mdl_close":
            await self._answer(cb_id, "✖ Closed")
            try:
                await self.channel._api_call(
                    "deleteMessage",
                    {"chat_id": chat_id, "message_id": message_id},
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Exception suppressed: %s", exc)
            return

        if cb_data == "mdl_chgprov":
            await self._answer(cb_id, "")
            await self.channel._handle_providers(chat_id, user_id, message_id=message_id)
            return

        # ── Activate provider from models picker: mdl_prov_{prov_id} ────────
        if cb_data.startswith("mdl_prov_"):
            prov_id = cb_data[len("mdl_prov_") :]
            try:
                from navig.providers.registry import get_provider

                manifest = get_provider(prov_id)
            except Exception:  # noqa: BLE001
                manifest = None

            if not manifest:
                await self._answer(cb_id, f"⚠️ Unknown provider: {prov_id}")
                return

            try:
                await self._activate_provider_with_defaults(
                    cb_id, chat_id, message_id, prov_id, manifest
                )
            except Exception as exc:
                logger.warning("Provider activation failed (mdl_prov_): %s", exc)
                await self._answer(cb_id, "⚠️ Activation failed — please try again", show_alert=True)
            return

        # ── Show model list for tier: mdl_tier_{prov_id}_{tier_code} ────────
        if cb_data.startswith("mdl_tier_"):
            rest = cb_data[len("mdl_tier_") :]
            # tier_code is always a single char at the end
            if len(rest) < 2 or rest[-2] != "_":
                await self._answer(cb_id, "⚠️ Bad tier callback")
                return
            prov_id = rest[:-2]
            tier_code = rest[-1]
            if not prov_id:
                await self._answer(cb_id, "⚠️ Bad tier callback")
                return
            if tier_code not in _TIER_CODE_MAP:
                await self._answer(cb_id, "⚠️ Unknown tier")
                return
            await self._answer(cb_id, "")
            await self.channel._show_models_model_list(
                chat_id,
                prov_id,
                tier_code,
                page=0,
                message_id=message_id,
            )
            return

        # ── Select model: mdl_sel_{prov_id}_{idx}_{tier_code}_{page} ────────
        if cb_data.startswith("mdl_sel_"):
            rest = cb_data[len("mdl_sel_") :]
            parts = rest.rsplit("_", 3)
            if len(parts) != 4:
                await self._answer(cb_id, "⚠️ Bad selection callback")
                return
            prov_id, idx_str, tier_code, page_str = parts

            if not prov_id:
                await self._answer(cb_id, "⚠️ Bad selection callback")
                return
            if tier_code not in _TIER_CODE_MAP:
                await self._answer(cb_id, "⚠️ Unknown tier")
                return
            tier, tier_label = _TIER_CODE_MAP[tier_code]

            try:
                model_idx = int(idx_str)
            except ValueError:
                await self._answer(cb_id, "⚠️ Bad model index")
                return
            try:
                page = int(page_str)
            except ValueError:
                await self._answer(cb_id, "⚠️ Bad page index")
                return
            if page < 0:
                await self._answer(cb_id, "⚠️ Bad page index")
                return

            # Resolve models
            models_list: list = []
            try:
                from navig.providers.registry import get_provider

                manifest = get_provider(prov_id)
            except Exception:
                manifest = None
            if hasattr(self.channel, "_resolve_provider_models"):
                try:
                    models_list = await self.channel._resolve_provider_models(
                        prov_id, manifest=manifest
                    )
                except Exception:
                    logger.warning("Provider model resolution failed for mdl_sel_%s", prov_id)
                    await self._answer(
                        cb_id, "⚠️ Could not load models for this provider", show_alert=True
                    )
                    return

            if model_idx < 0 or model_idx >= len(models_list):
                await self._answer(cb_id, "⚠️ Model index out of range")
                return
            model = models_list[model_idx]

            # Update hybrid router (optional)
            try:
                from navig.agent.ai_client import get_ai_client

                router = get_ai_client().model_router
                if router and router.is_active:
                    slot = router.cfg.slot_for_tier(tier)
                    slot.provider = prov_id
                    slot.model = model
                    if hasattr(self.channel, "_persist_hybrid_router_assignments"):
                        self.channel._persist_hybrid_router_assignments(router.cfg)
            except Exception:
                logger.debug("Hybrid router update skipped for mdl_sel_")

            # Always update LLM Mode Router
            mode_router_saved = True
            try:
                if hasattr(self.channel, "_update_llm_mode_router"):
                    self.channel._update_llm_mode_router(prov_id, {tier: model})
            except Exception:  # noqa: BLE001
                mode_router_saved = False
                logger.warning("LLM mode router update failed for mdl_sel_; model not saved")

            short_model = model.split("/")[-1].split(":")[-1]
            if mode_router_saved:
                await self._answer(cb_id, f"✅ {tier_label} → {short_model[:40]}")
            else:
                await self._answer(cb_id, "⚠️ Model selection could not be saved", show_alert=True)

            try:
                from navig.commands.init import mark_chat_onboarding_step_completed

                mark_chat_onboarding_step_completed("ai-provider")
            except (ImportError, AttributeError, TypeError, ValueError) as exc:
                logger.debug("Exception suppressed in chat onboarding step: %s", exc)

            # Refresh model list showing new ✅
            try:
                await self.channel._show_models_model_list(
                    chat_id,
                    prov_id,
                    tier_code,
                    page=page,
                    message_id=message_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Model list refresh skipped after successful mdl_sel_: %s", exc)
            return

        # ── Paginate: mdl_page_{prov_id}_{tier_code}_{page} ────────────────
        if cb_data.startswith("mdl_page_"):
            rest = cb_data[len("mdl_page_") :]
            parts = rest.rsplit("_", 2)
            if len(parts) != 3:
                await self._answer(cb_id, "⚠️ Bad page callback")
                return
            prov_id, tier_code, page_str = parts
            if not prov_id:
                await self._answer(cb_id, "⚠️ Bad page callback")
                return
            if tier_code not in _TIER_CODE_MAP:
                await self._answer(cb_id, "⚠️ Unknown tier")
                return
            try:
                page = int(page_str)
            except ValueError:
                await self._answer(cb_id, "⚠️ Bad page index")
                return
            if page < 0:
                await self._answer(cb_id, "⚠️ Bad page index")
                return
            await self._answer(cb_id, "")
            await self.channel._show_models_model_list(
                chat_id,
                prov_id,
                tier_code,
                page=page,
                message_id=message_id,
            )
            return

        # ── Back to tier summary: mdl_back_tiers_{prov_id} ─────────────────
        if cb_data.startswith("mdl_back_tiers_"):
            prov_id = cb_data[len("mdl_back_tiers_") :]
            await self._answer(cb_id, "")
            await self.channel._show_models_tier_summary(
                chat_id,
                prov_id,
                message_id=message_id,
            )
            return

        # Fallback
        await self._answer(cb_id, "⚠️ Unknown models action")

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
            # Delete the old trace message before sending a fresh one to avoid accumulation
            await self.channel._api_call(
                "deleteMessage",
                {"chat_id": chat_id, "message_id": message_id},
            )
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
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Exception suppressed: %s", exc
                )  # best-effort; failure is non-critical
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
                        f"📝 <b>Transcript:</b>\n{html.escape(transcript)}",
                        parse_mode="HTML",
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
                f"ℹ️ <b>File Info</b>\n"
                f"Title: {html.escape(title)}\n"
                f"Artist: {html.escape(performer)}\n"
                f"Duration: {dur_str}\n"
                f"Size: {size_str}\n"
                f"MIME: <code>{html.escape(mime_type)}</code>\n"
                f"Type: {kind}"
            )
            await self.channel.send_message(chat_id, info_text, parse_mode="HTML")
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
                            f"🌐 Detected language: <b>{html.escape(lang_reply or 'Unknown')}</b>",
                            parse_mode="HTML",
                        )
                    except Exception as _lde:  # noqa: BLE001
                        logger.debug("audmsg:lang inner error: %s", _lde)
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

    async def _handle_evening_callback(
        self,
        cb_id: str,
        cb_data: str,
        chat_id: int,
        message_id: int,
        user_id: int,
    ) -> None:
        """Handle evening summary inline action buttons (eve:{action}).

        Actions
        -------
        log_shipped   — prompts user to briefly log what was completed today
        plan_tomorrow — prompts user to set their top priority for tomorrow
        backup_check  — runs ``navig backup show`` in-process and replies
        dnd_on        — activates DND / quiet mode for the rest of the night
        """
        import asyncio as _asyncio

        action = cb_data.split(":", 1)[1] if ":" in cb_data else cb_data

        if action == "log_shipped":
            await self._answer(cb_id, "✅ What shipped?")
            await self.channel.send_message(
                chat_id,
                (
                    "📦 <b>What shipped today?</b>\n\n"
                    "Reply with a quick line — one sentence per item is enough.\n"
                    "<i>e.g. Fixed login bug · Deployed v2.3 · Reviewed 4 PRs</i>"
                ),
                parse_mode="HTML",
            )
            return

        if action == "plan_tomorrow":
            await self._answer(cb_id, "🎯 Set your priority")
            await self.channel.send_message(
                chat_id,
                (
                    "🎯 <b>What's the top priority for tomorrow?</b>\n\n"
                    "Reply with a single sentence — I'll log it as your morning anchor.\n"
                    "<i>e.g. Ship the auth refactor · Unblock the design review</i>"
                ),
                parse_mode="HTML",
            )
            return

        if action == "backup_check":
            await self._answer(cb_id, "💾 Checking...")
            try:
                import subprocess as _subprocess

                result = await _asyncio.to_thread(
                    lambda: _subprocess.run(
                        ["navig", "backup", "show", "--plain"],
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                )
                out = (result.stdout or "").strip()
                err = (result.stderr or "").strip()
                body = out or err or "No backup data found."
                # Truncate to stay inside Telegram limits
                if len(body) > 3500:
                    body = body[:3500] + "\n…(truncated)"
                await self.channel.send_message(
                    chat_id,
                    f"💾 <b>Backup status</b>\n\n<pre>{html.escape(body)}</pre>",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("eve:backup_check error: %s", exc)
                await self.channel.send_message(
                    chat_id,
                    "⚠️ Could not retrieve backup status. Run <code>navig backup show</code> manually.",
                    parse_mode="HTML",
                )
            return

        if action == "dnd_on":
            await self._answer(cb_id, "🌑 Going dark")
            try:
                from navig.agent.proactive.user_state import get_user_state_tracker

                tracker = get_user_state_tracker()
                tracker.set_dnd(True)
                await self.channel.send_message(
                    chat_id,
                    "🌑 <b>DND activated.</b>  Notifications suppressed until morning.\n"
                    "<i>Send any message to wake me up early.</i>",
                    parse_mode="HTML",
                )
            except Exception as exc:
                logger.warning("eve:dnd_on error: %s", exc)
                await self.channel.send_message(
                    chat_id,
                    "🌑 DND activated. Notifications will be suppressed.",
                )
            return

        # Unknown action — acknowledge silently
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
            logger.warning("Settings session error: %s", exc)
            await self._answer(cb_id, "⚠️ Session error — please try again")
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
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Exception suppressed: %s", exc
                )  # best-effort; failure is non-critical
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
            "st_goto_voice_provider": "_handle_provider_voice",
            "st_goto_providers": "_handle_providers_and_models",
            # _handle_mode_menu does not exist — the correct method is _handle_mode
            "st_goto_focus": "_handle_mode",
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
                    if cb_data == "st_goto_model":
                        # _handle_models_command accepts message_id — pass it for in-place edit
                        await method(chat_id, user_id, message_id=message_id)
                    elif cb_data == "st_goto_trace":
                        # _handle_trace has no message_id param
                        await method(chat_id, user_id)
                    elif cb_data == "st_goto_focus":
                        # _handle_mode signature: (chat_id, text="", user_id=0)
                        await method(chat_id, "", user_id=user_id)
                    else:
                        try:
                            await method(
                                chat_id,
                                user_id,
                                is_group,
                                message_id=message_id,
                            )
                        except TypeError as exc:
                            err = str(exc)
                            signature_mismatch = (
                                "unexpected keyword argument" in err
                                or "positional argument" in err
                                or "required positional argument" in err
                            )
                            if signature_mismatch:
                                await method(chat_id)
                            else:
                                raise
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
        except Exception as exc:  # noqa: BLE001
            logger.debug("Exception suppressed: %s", exc)  # best-effort; failure is non-critical

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
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": keyboard_rows},
                },
            )
        except Exception as exc:
            logger.debug("Audio settings refresh failed: %s", exc)

    async def _handle_dig_deeper_callback(
        self,
        cb_id: str,
        chat_id: int,
        user_id: int,
        cb_key: str,
        entry: "CallbackEntry",
    ) -> None:
        """
        Run the LLM with _DIG_DEEPER_PROMPT, extract an AI-generated follow-up
        question from the response (via _FOLLOWUP_EXTRACT_RE), strip the marker
        line from the displayed text, and attach an ↗️ Ask this button.
        """
        await self._answer(cb_id, "📖 Going deeper…")
        typing_task = asyncio.create_task(self.channel._keep_typing(chat_id))
        try:
            prompt = _DIG_DEEPER_PROMPT.format(
                user_message=entry.user_message,
                ai_response=entry.ai_response[:2500],
            )
            response = await self._get_ai_response(prompt, user_id)
            if not response:
                await self.channel.send_message(chat_id, "❌ Couldn't generate a deeper response.")
                return

            # Extract and strip the follow-up question from the response.
            followup_question: str | None = None
            display_response = response
            match = _FOLLOWUP_EXTRACT_RE.search(response)
            if match:
                followup_question = match.group(1).strip()
                display_response = response[: match.start()].rstrip()

            # Build keyboard from the expanded response.
            builder = ResponseKeyboardBuilder(self.store)
            keyboard = builder.build(
                ai_response=display_response,
                user_message=entry.user_message,
                message_id=0,
            )

            # Append ↗️ Ask this button if a follow-up question was extracted.
            if followup_question:
                followup_cb = _FOLLOWUP_CB_PREFIX + cb_key
                if len(followup_cb) <= MAX_CALLBACK_DATA:
                    self.store.put(
                        _FOLLOWUP_STORE_PREFIX + cb_key,
                        followup_question,
                    )
                    followup_row = [{"text": _FOLLOWUP_BTN_LABEL, "callback_data": followup_cb}]
                    if keyboard and "inline_keyboard" in (keyboard or {}):
                        keyboard["inline_keyboard"].append(followup_row)
                    else:
                        keyboard = {"inline_keyboard": [followup_row]}

            await self.channel.send_message(chat_id, display_response, keyboard=keyboard)
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError as exc:
                logger.debug("Exception suppressed (typing task cancelled): %s", exc)

    async def _handle_ask_followup_callback(
        self,
        cb_id: str,
        chat_id: int,
        user_id: int,
        followup_key: str,
    ) -> None:
        """
        Retrieve the stored follow-up question and route it through on_message()
        as if the user typed it, then send the result back to the chat.
        """
        question = self.store.get(followup_key)
        if not question:
            await self._answer(cb_id, "⏳ Question expired")
            return

        await self._answer(cb_id, "")
        typing_task = asyncio.create_task(self.channel._keep_typing(chat_id))
        try:
            response = await self._get_ai_response(str(question), user_id)
            if response:
                builder = ResponseKeyboardBuilder(self.store)
                keyboard = builder.build(
                    ai_response=response,
                    user_message=str(question),
                    message_id=0,
                )
                await self.channel.send_message(chat_id, response, keyboard=keyboard)
            else:
                await self.channel.send_message(chat_id, "❌ Couldn't answer the follow-up question.")
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError as exc:
                logger.debug("Exception suppressed (typing task cancelled): %s", exc)

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
