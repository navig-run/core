"""
Telegram Card Navigator — navig gateway channel module.

Splits long LLM responses into paginated "reasoning cards" and renders
them with prev/next navigation inline keyboards.  The last card includes
extra actions: ✅ Accept · ♻️ Refine · 📋 Copy All.

Sessions are stored ephemerally in CallbackStore; no extra DB required.

Callback protocol:
  card:next:<KEY>       — advance to next card
  card:prev:<KEY>       — go to previous card
  card:jump:<KEY>:<N>   — jump to card index N (0-based)
  card:copy:<KEY>       — return full text as a single message
  card:accept:<KEY>     — dismiss navigator, confirm output
  card:refine:<KEY>     — hand off to RefinementEngine

Usage::

    from navig.gateway.channels.telegram_navigator import CardNavigator

    nav = CardNavigator(channel)
    session = await nav.create(chat_id, user_id, topic, llm_text, send_fn)
"""

from __future__ import annotations

import json
import html
import logging
import re
import textwrap
import time
import uuid
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────

MAX_CARD_CHARS = 3_800  # safe Telegram limit (4096 with nav header overhead)
CARD_SPLIT_PRIORITY = ["paragraph", "sentence", "hard"]


# ─────────────────────────────────────────────────────────────────
# Card splitting
# ─────────────────────────────────────────────────────────────────


def split_into_cards(
    text: str,
    max_chars: int = MAX_CARD_CHARS,
) -> list[str]:
    """
    Split *text* into a list of cards each ≤ *max_chars*.

    Strategy priority:
    1. paragraph boundaries (``\\n\\n``)
    2. sentence boundaries (``. ``, ``! ``, ``? ``)
    3. hard word‑wrap at *max_chars*
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = re.split(r"\n{2,}", text)
    chunks: list[str] = []
    current = ""

    for para in paragraphs:
        candidate = (current + "\n\n" + para).lstrip("\n") if current else para

        if len(candidate) <= max_chars:
            current = candidate
            continue

        # Paragraph alone is too large → split on sentences
        if len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sentence in sentences:
                if len(current) + len(sentence) + 1 <= max_chars:
                    current = (current + " " + sentence).strip()
                else:
                    if current:
                        chunks.append(current)
                    if len(sentence) > max_chars:
                        # Hard split
                        for i in range(0, len(sentence), max_chars):
                            chunks.append(sentence[i : i + max_chars])
                        current = ""
                    else:
                        current = sentence
        else:
            # Flush current, start new card with this paragraph
            if current:
                chunks.append(current)
            current = para

    if current:
        chunks.append(current)

    # Final pass: ensure nothing exceeds max_chars (hard-split stragglers)
    final: list[str] = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final.append(chunk)
        else:
            final.extend(chunk[i : i + max_chars] for i in range(0, len(chunk), max_chars))
    return final


# ─────────────────────────────────────────────────────────────────
# CardSession dataclass
# ─────────────────────────────────────────────────────────────────


@dataclass
class CardSession:
    """Ephemeral session stored in CallbackStore extra."""

    cards: list[str]
    current: int
    chat_id: int
    user_id: int
    message_id: int | None
    topic: str
    session_key: str  # CallbackStore key prefix "nav:<uuid>"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CardSession:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def serialise(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def deserialise(cls, s: str) -> CardSession:
        return cls.from_dict(json.loads(s))


# ─────────────────────────────────────────────────────────────────
# Keyboard builder
# ─────────────────────────────────────────────────────────────────


def build_nav_keyboard(
    session: CardSession,
    idx: int,
) -> list[list[dict]]:
    """
    Build inline keyboard for card at index *idx*.

    Normal card:  [← Prev] [Card X/N] [Next →]
    Last card extras: [✅ Accept] [♻️ Refine] [📋 Copy All]
    """
    key = session.session_key
    total = len(session.cards)
    rows: list[list[dict]] = []

    # Navigation row
    nav_row: list[dict] = []
    if idx > 0:
        nav_row.append({"text": "◀ Prev", "callback_data": f"card:prev:{key}"})
    nav_row.append({"text": f"📄 {idx + 1}/{total}", "callback_data": f"card:jump:{key}:{idx}"})
    if idx < total - 1:
        nav_row.append({"text": "Next ▶", "callback_data": f"card:next:{key}"})
    rows.append(nav_row)

    # Last card action row
    if idx == total - 1:
        rows.append(
            [
                {"text": "✅ Accept", "callback_data": f"card:accept:{key}"},
                {"text": "♻️ Refine", "callback_data": f"card:refine:{key}"},
                {"text": "📋 All", "callback_data": f"card:copy:{key}"},
            ]
        )

    return rows


def _card_header(idx: int, total: int, topic: str) -> str:
    topic_short = textwrap.shorten(topic, width=40, placeholder="…") if topic else ""
    header = f"<b>{'💭 ' if topic_short else ''}思 Card {idx + 1} of {total}</b>"
    if topic_short:
        header = f"<b>💭 {html.escape(topic_short)}</b> — card {idx + 1}/{total}"
    return header


def _format_card(session: CardSession, idx: int) -> str:
    header = _card_header(idx, len(session.cards), session.topic)
    return f"{header}\n\n{session.cards[idx]}"


# ─────────────────────────────────────────────────────────────────
# CardNavigator
# ─────────────────────────────────────────────────────────────────

SendFn = Callable[..., Coroutine[Any, Any, Any]]


class CardNavigator:
    """
    Manages reasoning card sessions for a Telegram channel.

    Parameters
    ----------
    channel:
        The ``TelegramChannel`` (or compatible) instance that owns
        ``send_message`` / ``edit_message`` methods.
    cb_store:
        A ``CallbackStore`` instance from ``telegram_keyboards``.
        If not provided, one will be retrieved via
        ``get_callback_store()``.
    """

    def __init__(self, channel: Any, cb_store: Any = None):
        self.channel = channel
        self._store = cb_store

    def _get_store(self) -> Any:
        if self._store is not None:
            return self._store
        try:
            from navig.gateway.channels.telegram_keyboards import get_callback_store

            return get_callback_store()
        except Exception as exc:
            logger.error("CardNavigator: cannot get CallbackStore: %s", exc)
            raise

    # ── Public API ───────────────────────────────────────────────

    async def create(
        self,
        chat_id: int,
        user_id: int,
        topic: str,
        llm_text: str,
        send_fn: SendFn | None = None,
    ) -> CardSession:
        """
        Split *llm_text* into cards and send the first card to *chat_id*.

        Returns the created ``CardSession``.
        """
        cards = split_into_cards(llm_text)
        session_key = f"nav:{uuid.uuid4().hex[:12]}"
        session = CardSession(
            cards=cards,
            current=0,
            chat_id=chat_id,
            user_id=user_id,
            message_id=None,
            topic=topic,
            session_key=session_key,
        )

        # Persist to CallbackStore with 2h TTL
        store = self._get_store()
        store.put(session_key, {"session": session.serialise()}, ttl=7200)

        if send_fn is None:
            send_fn = self.channel.send_message

        msg_id = await self._send_card(send_fn, session, 0)
        session.message_id = msg_id

        # Update store with message_id
        store.put(session_key, {"session": session.serialise()}, ttl=7200)
        return session

    async def go_to(
        self,
        session: CardSession,
        idx: int,
    ) -> None:
        """Navigate session to card *idx*, editing the existing message."""
        idx = max(0, min(idx, len(session.cards) - 1))
        session.current = idx
        keyboard = build_nav_keyboard(session, idx)
        text = _format_card(session, idx)

        if session.message_id is not None:
            try:
                await self.channel.edit_message(
                    session.chat_id,
                    session.message_id,
                    text,
                    parse_mode="HTML",
                    keyboard=keyboard,
                )
                return
            except Exception as exc:
                logger.warning("CardNavigator.go_to edit failed: %s", exc)

        # Fallback: send new
        msg = await self.channel.send_message(
            session.chat_id,
            text,
            parse_mode="HTML",
            keyboard=keyboard,
        )
        if isinstance(msg, dict):
            session.message_id = msg.get("message_id")

    # ── Internal ─────────────────────────────────────────────────

    async def _send_card(self, send_fn: SendFn, session: CardSession, idx: int) -> int | None:
        keyboard = build_nav_keyboard(session, idx)
        text = _format_card(session, idx)
        try:
            result = await send_fn(
                session.chat_id,
                text,
                parse_mode="HTML",
                keyboard=keyboard,
            )
            if isinstance(result, dict):
                return result.get("message_id")
            return None
        except Exception as exc:
            logger.error("CardNavigator._send_card failed: %s", exc)
            return None


# ─────────────────────────────────────────────────────────────────
# Callback handler (registered in CallbackHandler.handle)
# ─────────────────────────────────────────────────────────────────


async def handle_card_callback(
    channel: Any,
    callback_query: Any,
    cb_store: Any,
) -> None:
    """
    Dispatch ``card:*`` callback data.

    Expected callback_data formats:
      ``card:next:<KEY>``
      ``card:prev:<KEY>``
      ``card:jump:<KEY>:<N>``
      ``card:copy:<KEY>``
      ``card:accept:<KEY>``
      ``card:refine:<KEY>``
    """
    def _cb_get(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    async def _answer_callback(callback_id: str, text: str = "") -> None:
        if not callback_id:
            return
        answer_fn = getattr(channel, "answer_callback_query", None)
        if callable(answer_fn):
            try:
                await answer_fn(callback_id, text)
                return
            except Exception:
                pass
        api_call = getattr(channel, "_api_call", None)
        if callable(api_call):
            try:
                await api_call(
                    "answerCallbackQuery",
                    {"callback_query_id": callback_id, "text": text, "show_alert": False},
                )
            except Exception:
                pass

    cb_id = _cb_get(callback_query, "id", "") or ""
    cb_data: str = _cb_get(callback_query, "data", "") or ""
    chat_id: int = 0
    message_id: int | None = None

    msg = _cb_get(callback_query, "message", None)
    if msg:
        chat = _cb_get(msg, "chat", None)
        chat_id = _cb_get(chat, "id", 0) or 0
        message_id = _cb_get(msg, "message_id", None)

    # Parse: card:<action>:<key>[:<extra>]
    parts = cb_data.split(":", 3)
    if len(parts) < 3:
        return
    action = parts[1]
    session_key = parts[2]
    extra = parts[3] if len(parts) > 3 else ""

    # Load session
    entry = cb_store.get(session_key)
    if not entry:
        try:
            await _answer_callback(cb_id, "⚠️ Session expired")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        return

    try:
        session = CardSession.deserialise(entry.get("session", "{}"))
    except Exception as exc:
        logger.warning("handle_card_callback: deserialise error: %s", exc)
        await _answer_callback(cb_id, "⚠️ Session error")
        return

    nav = CardNavigator(channel, cb_store)

    if action == "next":
        await nav.go_to(session, session.current + 1)
        cb_store.put(session_key, {"session": session.serialise()}, ttl=7200)

    elif action == "prev":
        await nav.go_to(session, session.current - 1)
        cb_store.put(session_key, {"session": session.serialise()}, ttl=7200)

    elif action == "jump":
        try:
            idx = int(extra)
            await nav.go_to(session, idx)
            cb_store.put(session_key, {"session": session.serialise()}, ttl=7200)
        except ValueError:
            pass  # malformed value; skip

    elif action == "copy":
        full_text = "\n\n───\n\n".join(session.cards)
        await channel.send_message(chat_id, full_text)
        await _answer_callback(cb_id, "📋 Full text sent")

    elif action == "accept":
        # Edit card to accepted state
        text = _format_card(session, session.current) + "\n\n✅ _Accepted_"
        try:
            await channel.edit_message(
                chat_id,
                session.message_id or message_id,
                text,
                parse_mode="HTML",
                keyboard=[],
            )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        cb_store.remove(session_key)
        await _answer_callback(cb_id, "✅ Accepted")

    elif action == "refine":
        # Hand off to RefinementEngine
        try:
            from navig.gateway.channels.telegram_refiner import RefinementEngine

            full_text = "\n\n".join(session.cards)
            refiner = RefinementEngine(channel, cb_store)
            await _answer_callback(cb_id, "♻️ Starting refinement…")
            await refiner.start(
                chat_id=session.chat_id,
                user_id=session.user_id,
                text=full_text,
                topic=session.topic,
            )
        except Exception as exc:
            logger.error("handle_card_callback refine error: %s", exc)
            await _answer_callback(cb_id, "⚠️ Refine error")
    else:
        await _answer_callback(cb_id, "")
