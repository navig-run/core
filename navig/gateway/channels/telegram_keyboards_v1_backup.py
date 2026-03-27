"""
Telegram Inline Keyboard Builder for NAVIG Gateway

Dynamically generates contextual InlineKeyboardMarkup buttons
for every AI response based on content analysis.

Button types:
- Quick Reply Suggestions — AI-predicted follow-up questions
- Action Triggers — Regenerate, Summarize, Translate, Save
- Rating/Feedback — thumbs up/down + improve
- Structured Navigation — Pros/Cons/Details tabs for comparisons
- Expandable Sections — "Show more" for long responses

Callback data schema:  action:context:id
  action   = button action type
  context  = short context hash or category
  id       = unique identifier (message_id or sequence)
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
MAX_ROWS = 4
MAX_BUTTON_TEXT = 30
MAX_CALLBACK_DATA = 64  # Telegram limit


class ContentCategory(str, Enum):
    """Categories used to decide which buttons to attach."""

    INFORMATIONAL = "info"
    COMPARISON = "compare"
    HOWTO = "howto"
    CODE = "code"
    OPINION = "opinion"
    LIST = "list"
    CONVERSATIONAL = "chat"
    ERROR = "error"


# ────────────────────────────────────────────────────────────────
# Content analyser (fast, regex-based — no LLM call)
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

    # Count list items
    numbered = len(_NUMBERED_LIST.findall(text))
    bulleted = len(_BULLET_LIST.findall(text))
    if numbered >= 3 or bulleted >= 4:
        return ContentCategory.LIST

    if len(text) > 600:
        return ContentCategory.INFORMATIONAL

    return ContentCategory.CONVERSATIONAL


# ────────────────────────────────────────────────────────────────
# Followup generator (fast heuristic, no LLM)
# ────────────────────────────────────────────────────────────────

_FOLLOWUP_TEMPLATES: dict[ContentCategory, list[str]] = {
    ContentCategory.CODE: [
        "🔍 Explain this code",
        "🧪 Add unit tests",
        "♻️ Refactor this",
        "📖 Add docstrings",
    ],
    ContentCategory.COMPARISON: [
        "📊 Summarize differences",
        "✅ Which is better?",
        "💡 Show examples",
        "🔀 More alternatives",
    ],
    ContentCategory.HOWTO: [
        "🔎 More detail",
        "⚡ Simpler version",
        "🛠️ Common pitfalls",
        "📋 Checklist format",
    ],
    ContentCategory.INFORMATIONAL: [
        "📝 Summarize shorter",
        "🔍 Go deeper",
        "💡 Practical examples",
        "❓ Related topics",
    ],
    ContentCategory.OPINION: [
        "🤔 Counter-argument",
        "📊 Show data/sources",
        "🔀 Other perspectives",
        "✅ Action plan",
    ],
    ContentCategory.LIST: [
        "🔍 Detail first item",
        "📊 Compare top 3",
        "⭐ Rank by priority",
        "📋 Export as list",
    ],
    ContentCategory.CONVERSATIONAL: [
        "💡 Tell me more",
        "🔍 Give an example",
        "❓ Why?",
    ],
    ContentCategory.ERROR: [
        "🔄 Try again",
        "💡 Rephrase my question",
        "📝 Help me ask better",
    ],
}


def _short_hash(text: str, length: int = 6) -> str:
    """Generate a short hash from text for callback data."""
    return hashlib.md5(text.encode()).hexdigest()[:length]


# ────────────────────────────────────────────────────────────────
# Callback data store (in-memory, bounded)
# ────────────────────────────────────────────────────────────────


@dataclass
class CallbackEntry:
    """Stored context for a callback button."""

    action: str
    user_message: str  # original user message
    ai_response: str  # full AI response text
    category: str  # ContentCategory value
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


class CallbackStore:
    """
    Bounded in-memory store mapping callback_data IDs → full context.

    Telegram limits callback_data to 64 bytes, so we store the real
    payload here and put only a short key in the button.
    """

    def __init__(self, max_entries: int = 500):
        self._store: dict[str, CallbackEntry] = {}
        self._max = max_entries

    def put(self, key: str, entry: CallbackEntry) -> None:
        if len(self._store) >= self._max:
            # Evict oldest 20%
            items = sorted(self._store.items(), key=lambda kv: kv[1].created_at)
            to_remove = len(items) // 5 or 1
            for k, _ in items[:to_remove]:
                del self._store[k]
        self._store[key] = entry

    def get(self, key: str) -> CallbackEntry | None:
        return self._store.get(key)

    def remove(self, key: str) -> None:
        self._store.pop(key, None)


# Global store instance
_callback_store = CallbackStore()


def get_callback_store() -> CallbackStore:
    return _callback_store


# ────────────────────────────────────────────────────────────────
# ResponseKeyboardBuilder
# ────────────────────────────────────────────────────────────────


class ResponseKeyboardBuilder:
    """
    Analyse an AI response and build an InlineKeyboardMarkup
    (as a list-of-lists-of-dicts matching the Telegram API shape).

    Usage::

        builder = ResponseKeyboardBuilder()
        keyboard = builder.build(
            ai_response="Here's a Python snippet ...",
            user_message="write a function",
            message_id=12345,
        )
        # keyboard is List[List[Dict]] or None
    """

    def __init__(
        self,
        store: CallbackStore | None = None,
        include_followups: bool = True,
        include_actions: bool = True,
        include_feedback: bool = True,
        include_expand: bool = True,
    ):
        self.store = store or get_callback_store()
        self.include_followups = include_followups
        self.include_actions = include_actions
        self.include_feedback = include_feedback
        self.include_expand = include_expand

    # ── Public API ──

    def build(
        self,
        ai_response: str,
        user_message: str = "",
        message_id: int = 0,
        category_hint: str | None = None,
    ) -> list[list[dict[str, str]]] | None:
        """
        Build the keyboard for an AI response.

        Returns None if there's nothing useful to show.
        """
        category = (
            ContentCategory(category_hint)
            if category_hint and category_hint in ContentCategory._value2member_map_
            else classify_response(ai_response)
        )

        msg_hash = _short_hash(f"{user_message}:{message_id}")
        rows: list[list[dict[str, str]]] = []

        # 1. Quick Reply Suggestions (up to 1 row of 2-3 buttons)
        if self.include_followups:
            fup_row = self._build_followups(category, ai_response, user_message, msg_hash)
            if fup_row:
                rows.append(fup_row)

        # 2. Action Triggers (1 row)
        if self.include_actions:
            act_row = self._build_actions(category, msg_hash, user_message, ai_response)
            if act_row:
                rows.append(act_row)

        # 3. Structured Navigation (for comparisons / lists)
        if category in (ContentCategory.COMPARISON, ContentCategory.LIST):
            nav_row = self._build_navigation(category, msg_hash, user_message, ai_response)
            if nav_row:
                rows.append(nav_row)

        # 4. Expandable section (long responses)
        if self.include_expand and len(ai_response) > 1500:
            expand_row = self._build_expand(msg_hash, user_message, ai_response)
            if expand_row:
                rows.append(expand_row)

        # 5. Feedback row (always last, if enabled)
        if self.include_feedback:
            fb_row = self._build_feedback(msg_hash, user_message, ai_response)
            if fb_row:
                rows.append(fb_row)

        # Enforce MAX_ROWS
        rows = rows[:MAX_ROWS]

        return rows if rows else None

    # ── Private builders ──

    def _make_button(
        self,
        text: str,
        action: str,
        msg_hash: str,
        user_message: str = "",
        ai_response: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        """Create a single inline keyboard button dict."""
        # Truncate label
        label = text[:MAX_BUTTON_TEXT]

        # Build callback_data key
        cb_key = f"{action}:{msg_hash}"
        if len(cb_key) > MAX_CALLBACK_DATA:
            cb_key = cb_key[:MAX_CALLBACK_DATA]

        # Store context
        self.store.put(
            cb_key,
            CallbackEntry(
                action=action,
                user_message=user_message,
                ai_response=ai_response[:3000],  # cap stored response
                category=classify_response(ai_response).value,
                extra=extra or {},
            ),
        )

        return {"text": label, "callback_data": cb_key}

    def _build_followups(
        self,
        category: ContentCategory,
        ai_response: str,
        user_message: str,
        msg_hash: str,
    ) -> list[dict[str, str]]:
        """Generate 2-3 follow-up suggestion buttons."""
        templates = _FOLLOWUP_TEMPLATES.get(
            category, _FOLLOWUP_TEMPLATES[ContentCategory.CONVERSATIONAL]
        )
        # Pick 2-3 most relevant
        picks = templates[:3]

        row = []
        for i, label in enumerate(picks):
            btn = self._make_button(
                text=label,
                action=f"fup{i}",
                msg_hash=msg_hash,
                user_message=user_message,
                ai_response=ai_response,
                extra={"followup_text": label},
            )
            row.append(btn)
        return row[:MAX_BUTTONS_PER_ROW]

    def _build_actions(
        self,
        category: ContentCategory,
        msg_hash: str,
        user_message: str,
        ai_response: str,
    ) -> list[dict[str, str]]:
        """Generate contextual action buttons."""
        actions: list[tuple[str, str]] = []

        # Always offer regenerate
        actions.append(("🔄 Regenerate", "regen"))

        # Context-dependent actions
        if category == ContentCategory.CODE:
            actions.append(("📋 Copy code", "copy_code"))
            actions.append(("🧪 Add tests", "add_tests"))
        elif category in (ContentCategory.INFORMATIONAL, ContentCategory.HOWTO):
            actions.append(("📋 Summarize", "summarize"))
            actions.append(("🌐 Translate", "translate"))
        elif category == ContentCategory.COMPARISON:
            actions.append(("📊 Table format", "table_fmt"))
        elif category == ContentCategory.CONVERSATIONAL:
            actions.append(("🔍 Go deeper", "elaborate"))
        else:
            actions.append(("📋 Summarize", "summarize"))

        row = []
        for label, action in actions[:MAX_BUTTONS_PER_ROW]:
            btn = self._make_button(
                text=label,
                action=action,
                msg_hash=msg_hash,
                user_message=user_message,
                ai_response=ai_response,
            )
            row.append(btn)
        return row

    def _build_navigation(
        self,
        category: ContentCategory,
        msg_hash: str,
        user_message: str,
        ai_response: str,
    ) -> list[dict[str, str]]:
        """Generate structured navigation buttons for comparisons/lists."""
        if category == ContentCategory.COMPARISON:
            tabs = [
                ("📊 Pros", "nav_pros"),
                ("⚠️ Cons", "nav_cons"),
                ("💰 Bottom line", "nav_bottom"),
            ]
        else:  # LIST
            tabs = [
                ("🔝 Top picks", "nav_top"),
                ("📋 Full list", "nav_full"),
                ("⭐ Ranked", "nav_ranked"),
            ]

        row = []
        for label, action in tabs[:MAX_BUTTONS_PER_ROW]:
            btn = self._make_button(
                text=label,
                action=action,
                msg_hash=msg_hash,
                user_message=user_message,
                ai_response=ai_response,
            )
            row.append(btn)
        return row

    def _build_expand(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
    ) -> list[dict[str, str]]:
        """Show more / collapse button for long responses."""
        return [
            self._make_button(
                text="📖 Show full details ▼",
                action="expand",
                msg_hash=msg_hash,
                user_message=user_message,
                ai_response=ai_response,
            )
        ]

    def _build_feedback(
        self,
        msg_hash: str,
        user_message: str,
        ai_response: str,
    ) -> list[dict[str, str]]:
        """Rating/feedback row."""
        return [
            self._make_button("👍", "fb_up", msg_hash, user_message, ai_response),
            self._make_button("👎", "fb_down", msg_hash, user_message, ai_response),
            self._make_button("💡 Improve", "fb_improve", msg_hash, user_message, ai_response),
        ]


# ────────────────────────────────────────────────────────────────
# CallbackQueryHandler
# ────────────────────────────────────────────────────────────────

# Action → AI prompt template
_ACTION_PROMPTS: dict[str, str] = {
    "regen": (
        'The user asked: "{user_message}"\n'
        "Your previous answer was not satisfactory. "
        "Please provide a better, more complete answer."
    ),
    "summarize": ("Summarize the following response in 2-3 concise sentences:\n\n{ai_response}"),
    "translate": (
        "Translate the following text to the user's other language "
        "(if the text is in English, translate to Russian; if in Russian, translate to English):\n\n"
        "{ai_response}"
    ),
    "elaborate": (
        'The user asked: "{user_message}"\n'
        'Your previous answer was: "{ai_response_short}"\n'
        "Please elaborate with more detail, examples, and depth."
    ),
    "copy_code": None,  # special handler — extracts code blocks
    "add_tests": (
        "Given the following code, write comprehensive unit tests for it:\n\n{ai_response}"
    ),
    "table_fmt": (
        "Reformat the following comparison into a clear markdown table:\n\n{ai_response}"
    ),
    "expand": None,  # special handler — shows full text as edit
    # Navigation
    "nav_pros": (
        "From the following comparison, extract and list ONLY the advantages/pros:\n\n{ai_response}"
    ),
    "nav_cons": (
        "From the following comparison, extract and list ONLY the disadvantages/cons:\n\n"
        "{ai_response}"
    ),
    "nav_bottom": (
        "From the following comparison, give a concise bottom-line recommendation:\n\n{ai_response}"
    ),
    "nav_top": (
        "From the following list, pick the top 3 most important items and explain why:\n\n"
        "{ai_response}"
    ),
    "nav_full": (
        "Reformat the following into a clean numbered list with brief descriptions:\n\n"
        "{ai_response}"
    ),
    "nav_ranked": (
        "Rank the following items from most to least important, with brief justification:\n\n"
        "{ai_response}"
    ),
    # Feedback
    "fb_up": None,  # acknowledgement only
    "fb_down": None,  # acknowledgement only
    "fb_improve": (
        'The user asked: "{user_message}"\n'
        "Your previous answer was rated poorly. "
        "Please provide a significantly improved, more accurate and detailed answer."
    ),
}

# Followup actions (fup0, fup1, fup2) use the label text as a new message


class CallbackHandler:
    """
    Handle Telegram callback_query events (inline button presses).

    Routes each callback to the appropriate action: re-prompt the AI,
    extract code, send feedback acknowledgement, or edit the message.
    """

    def __init__(self, channel: TelegramChannel):
        self.channel = channel
        self.store = get_callback_store()

    async def handle(self, callback_query: dict[str, Any]) -> None:
        """
        Process a callback_query from Telegram.

        Args:
            callback_query: The callback_query object from the Telegram update.
        """
        cb_id = callback_query.get("id", "")
        cb_data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")
        user = callback_query.get("from", {})
        user_id = user.get("id")

        if not chat_id or not cb_data:
            await self._answer_callback(cb_id, "⚠️ Invalid callback")
            return

        # Look up stored context
        entry = self.store.get(cb_data)
        if not entry:
            await self._answer_callback(cb_id, "⏳ Button expired")
            return

        action = entry.action
        logger.info("Callback: action=%s user=%s chat=%s", action, user_id, chat_id)

        # ── Route by action ──

        # Followup suggestions → treat label as new user message
        if action.startswith("fup"):
            followup_text = entry.extra.get("followup_text", "Tell me more")
            # Strip emoji prefix for cleaner AI prompt
            clean_followup = re.sub(r"^[\U0001f300-\U0001f9ff\u2600-\u27bf]+\s*", "", followup_text)
            await self._answer_callback(cb_id, f"💬 {followup_text}")
            await self._send_as_new_message(chat_id, user_id, clean_followup)
            return

        # Feedback — just acknowledge
        if action == "fb_up":
            await self._answer_callback(cb_id, "👍 Thanks for the feedback!")
            logger.info("Feedback: positive for '%s'", entry.user_message[:50])
            return

        if action == "fb_down":
            await self._answer_callback(cb_id, "👎 Noted — I'll try harder next time")
            logger.info("Feedback: negative for '%s'", entry.user_message[:50])
            return

        # Copy code → extract code blocks and show them
        if action == "copy_code":
            code_blocks = _CODE_BLOCK.findall(entry.ai_response)
            if code_blocks:
                # Send just the code, stripped of fences
                code_text = "\n\n".join(block.strip("`").strip() for block in code_blocks)
                await self._answer_callback(cb_id, "📋 Code extracted")
                # Send as plain monospaced
                code_msg = f"```\n{code_text[:3900]}\n```"
                await self.channel.send_message(chat_id, code_msg)
            else:
                await self._answer_callback(cb_id, "No code blocks found")
            return

        # Expand → edit message to show full response
        if action == "expand":
            await self._answer_callback(cb_id, "📖 Expanding...")
            full_text = entry.ai_response[:4000]
            await self.channel.edit_message(
                chat_id=chat_id,
                message_id=message_id,
                text=full_text,
            )
            return

        # AI-routed actions → generate new response via AI
        prompt_template = _ACTION_PROMPTS.get(action)
        if prompt_template:
            await self._answer_callback(cb_id, "⏳ Working on it...")

            # Start typing indicator
            typing_task = asyncio.create_task(self.channel._keep_typing(chat_id))
            try:
                ai_response_short = entry.ai_response[:500]
                prompt = prompt_template.format(
                    user_message=entry.user_message,
                    ai_response=entry.ai_response[:2500],
                    ai_response_short=ai_response_short,
                )
                response = await self._get_ai_response(prompt, user_id, entry)
                if response:
                    # Build keyboard for the new response too
                    builder = ResponseKeyboardBuilder(self.store)
                    keyboard = builder.build(
                        ai_response=response,
                        user_message=entry.user_message,
                        message_id=message_id,
                    )
                    await self.channel.send_message(chat_id, response, keyboard=keyboard)
                else:
                    await self.channel.send_message(chat_id, "😅 Couldn't generate a response.")
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass  # task cancelled; expected during shutdown
            return

        # Unknown action
        await self._answer_callback(cb_id, "⚠️ Unknown action")

    async def _answer_callback(self, callback_id: str, text: str) -> None:
        """Answer a callback query (dismisses the loading spinner on the button)."""
        await self.channel._api_call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": False,
            },
        )

    async def _send_as_new_message(self, chat_id: int, user_id: int, text: str) -> None:
        """
        Route a followup as if the user typed a new message.

        This goes through the full gateway pipeline (routing, sessions, etc.)
        """
        if self.channel.on_message:
            typing_task = asyncio.create_task(self.channel._keep_typing(chat_id))
            try:
                response = await self.channel.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=text,
                    metadata={"chat_id": chat_id, "user_id": user_id},
                )
                if response:
                    builder = ResponseKeyboardBuilder(self.store)
                    keyboard = builder.build(
                        ai_response=response,
                        user_message=text,
                    )
                    await self.channel.send_message(chat_id, response, keyboard=keyboard)
            except Exception as e:
                logger.error("Followup failed: %s", e)
                await self.channel.send_message(chat_id, f"😅 Follow-up failed: {e}")
            finally:
                typing_task.cancel()
                try:
                    await typing_task
                except asyncio.CancelledError:
                    pass  # task cancelled; expected during shutdown

    async def _get_ai_response(self, prompt: str, user_id: int, entry: CallbackEntry) -> str | None:
        """Get AI response for an action prompt via the gateway pipeline."""
        if self.channel.on_message:
            try:
                return await self.channel.on_message(
                    channel="telegram",
                    user_id=str(user_id),
                    message=prompt,
                    metadata={"chat_id": 0, "user_id": user_id},
                )
            except Exception as e:
                logger.error("AI callback response failed: %s", e)
        return None
