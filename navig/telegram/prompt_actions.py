"""Telegram ↔ prompt library: ``/prompt``, the free-text sniff, and ``pr:`` buttons.

* ``/prompt`` with no argument shows favourites, or the most used prompts when
  there are no favourites.
* ``/prompt <words>`` with exactly one match sends that prompt; with several
  matches it shows a picker.
* Free text such as "send me the marketing poster prompt" is routed here only
  when the library actually has a match. Otherwise the message falls through to
  the LLM, which handles requests like "write me a prompt for…".
* Buttons use the ``pr:<action>:<key>`` format: ``o`` open · ``f`` favourite ·
  ``d`` as file · ``s`` next step. The key is
  :func:`navig.prompts.delivery.short_key`, so a button still works after a
  daemon restart.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# "send me the marketing poster prompt", "give me my debug prompt",
# "prompt for debugging", "the X prompt please", plus RU/FR forms.
_SNIFF_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:please\s+|pls\s+)?(?:send|give|show|get|fetch|find|open)\s+(?:me\s+)?"
        r"(?:the\s+|my\s+|a\s+|that\s+|our\s+)?(?P<q>.+?)\s+prompt\b",
        re.I,
    ),
    re.compile(
        r"^(?:please\s+)?(?:send|give|show|get|fetch|find|open)\s+(?:me\s+)?(?:the\s+|my\s+)?"
        r"prompts?\s+(?:for|about|called|named|on)\s+(?P<q>.+)$",
        re.I,
    ),
    re.compile(r"^prompts?\s*[:\-]\s*(?P<q>.+)$", re.I),
    re.compile(r"^(?:пришли|отправь|покажи|дай|найди)\s+(?:мне\s+)?промпт\w*\s+(?P<q>.+)$", re.I),
    re.compile(r"^(?:envoie|donne|montre|trouve)[- ]moi\s+(?:le\s+|mon\s+)?prompt\s+(?P<q>.+)$", re.I),
)
_TRAILING = re.compile(r"[\s.!?,;:]+$")


def sniff_prompt_request(text: str) -> str | None:
    """The search words if ``text`` asks for a saved prompt, else ``None``."""
    t = (text or "").strip()
    if not t or t.startswith("/") or len(t) > 200:
        return None
    for pat in _SNIFF_PATTERNS:
        m = pat.match(t)
        if m:
            q = _TRAILING.sub("", m.group("q")).strip(" \"'«»")
            q = re.sub(r"\s+(please|pls|again)$", "", q, flags=re.I)
            if q and q.lower() not in {"a", "the", "my", "new", "good", "some"}:
                return q
    return None


def _live() -> bool:
    try:
        from navig.gateway.channels.telegram_commands import command_is_live

        return command_is_live("prompt", "prompts")
    except Exception:  # noqa: BLE001 — fail-open like the extension gate itself
        return True


async def try_handle_free_text(
    channel: Any, chat_id: int, text: str, *, reply_to_message_id: int | None = None
) -> bool:
    """Handle "send me the X prompt" when the library has X. Returns True if handled."""
    query = sniff_prompt_request(text)
    if not query or not _live():
        return False
    from navig.store.prompt_library import get_prompt_store

    matches = get_prompt_store().resolve(query, limit=8)
    if not matches:
        return False  # not ours: let the LLM answer it
    await _deliver(channel, chat_id, query, matches, reply_to_message_id=reply_to_message_id)
    return True


async def handle_command(channel: Any, chat_id: int, argument: str) -> None:
    from navig.prompts import delivery
    from navig.store.prompt_library import get_prompt_store

    store = get_prompt_store()
    if store.count() == 0:
        await channel.send_message(
            chat_id,
            delivery._t("prompts.tg.empty", ""),
            parse_mode="HTML",
        )
        return
    if not argument:
        picks = store.list(favorite=True, sort="used", limit=delivery.PICK_LIMIT)
        if not picks:
            picks = store.list(sort="used", limit=delivery.PICK_LIMIT)
        await channel.send_message(
            chat_id, delivery.picker_html("", picks), parse_mode="HTML",
            keyboard=delivery.picker_keyboard(picks),
        )
        return
    matches = store.resolve(argument, limit=8)
    if not matches:
        await channel.send_message(
            chat_id,
            delivery._t("prompts.tg.none", "", query=_esc(argument)),
            parse_mode="HTML",
        )
        return
    await _deliver(channel, chat_id, argument, matches)


async def _deliver(
    channel: Any, chat_id: int, query: str, matches: list[Any], *, reply_to_message_id: int | None = None
) -> None:
    from navig.prompts import delivery

    if len(matches) == 1:
        ok = await delivery.send_prompt(channel, chat_id, matches[0], reply_to_message_id=reply_to_message_id)
        if not ok:
            await channel.send_message(
                chat_id,
                delivery._t("prompts.tg.failed", ""),
                parse_mode="HTML",
            )
        return
    await channel.send_message(
        chat_id, delivery.picker_html(query, matches), parse_mode="HTML",
        keyboard=delivery.picker_keyboard(matches), reply_to_message_id=reply_to_message_id,
    )


async def handle_callback(channel: Any, callback_query: dict[str, Any], data: str) -> None:
    """Route a ``pr:<action>:<key>`` button."""
    from navig.prompts import delivery
    from navig.store.prompt_library import get_prompt_store

    cq_id = callback_query.get("id")
    msg = callback_query.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    key = parts[2] if len(parts) > 2 else ""
    store = get_prompt_store()
    rec = delivery.from_short_key(key, store=store) if key else None

    async def answer(text: str = "") -> None:
        payload: dict[str, Any] = {"callback_query_id": cq_id}
        if text:
            payload["text"] = text
        try:
            await channel._api_call("answerCallbackQuery", payload)
        except Exception:  # noqa: BLE001 — an unanswered spinner is cosmetic
            logger.debug("answerCallbackQuery failed", exc_info=True)

    if rec is None or not chat_id:
        await answer(delivery._t("prompts.tg.gone", "That prompt no longer exists."))
        return
    if action == "f":
        rec = store.set_favorite(rec.id)
        await answer(
            delivery._t("prompts.tg.fav_on", "★ Added to favorites")
            if rec.favorite
            else delivery._t("prompts.tg.fav_off", "Removed from favorites")
        )
        try:
            await channel._api_call(
                "editMessageReplyMarkup",
                {
                    "chat_id": chat_id,
                    "message_id": msg.get("message_id"),
                    "reply_markup": {"inline_keyboard": delivery.prompt_keyboard(rec, store=store)},
                },
            )
        except Exception:  # noqa: BLE001 — the toast already confirmed it
            logger.debug("prompt keyboard refresh failed", exc_info=True)
        return
    await answer()
    if action in ("o", "s"):
        await delivery.send_prompt(channel, chat_id, rec, store=store)
    elif action == "d":
        await delivery.send_prompt(channel, chat_id, rec, as_file=True, store=store)


def _esc(text: str) -> str:
    import html

    return html.escape(text or "")
