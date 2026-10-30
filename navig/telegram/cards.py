"""Interactive Telegram cards: one message that edits itself instead of a chat full of copies.

A notification with a "View" button that sends a **second** message is not a card — it is
two messages, and the chat keeps both. This module is the house answer:

* one message per thing the operator has to look at, edited in place;
* navigation that always works — **Open** (or View) expands, **Back** returns,
  **Close** collapses the card to one closed line and drops the keyboard (Telegram
  cannot reliably delete a message for both sides, so collapsing is the house
  behaviour);
* a small registry so a plugin owns the *content* and core owns the *plumbing*::

      from navig.telegram.cards import register_card_action
      register_card_action("dist", handle, module="distribution")

  ``handle(action)`` receives a :class:`Action` (``prefix``, ``action``, ``key``, the
  channel, chat/message ids and, for a text reply, ``action.text``) and returns a
  :class:`Screen` to edit the card with, or ``None`` to leave it alone.

Callback payloads are ``cd:<prefix>:<action>:<key>`` — 64 bytes is Telegram's hard cap,
so :func:`encode` validates and a long key is a build-time error rather than a button
that silently does nothing. The gateway's Telegram channel routes every ``cd:`` press
here (:func:`dispatch`), **after** its own user/chat authorisation and extension gate;
this module answers every press (so no spinner is left running), refuses a press whose
plugin module is switched off, and edits the card in place.

The card buttons reach the chat through whichever transport is available: the live
channel when the gateway is handling a press (rate-limit handling included), otherwise
the Bot API directly — which is what lets a CLI *send* the first card without a gateway.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Every card button starts here. Keep in sync with the routing branch in
#: ``navig/gateway/channels/telegram.py`` and the Core extension's callback prefixes.
CALLBACK_PREFIX = "cd:"

#: Telegram rejects an over-length message; a detail view is clamped, never refused.
#: How long an identical card is considered already delivered to a chat.
CARD_DEDUPE_WINDOW_S: float = 600.0

#: A SUMMARY card is a digest; at most one per (owner, chat, local day). The review
#: card is built from live task rows, so a second run the same day changes its TEXT —
#: the 10-minute fingerprint would miss it, and the operator would see two reviews.
CARD_DAILY_WINDOW_S: float = 20 * 3600


def _local_day() -> str:
    """The operator's local calendar day — the unit a digest card belongs to."""
    return time.strftime("%Y-%m-%d")


TEXT_LIMIT = 4000

#: ``.. `` is what a closed card reads as. One line, no keyboard.
CLOSED_TEXT = "✓ Closed"

SUMMARY = "summary"
DETAIL = "detail"
RESULT = "result"
_KINDS = frozenset({SUMMARY, DETAIL, RESULT})

#: How long a "reply with the reason" prompt waits for the operator's next message.
REPLY_TTL_S = 15 * 60


@dataclass(frozen=True)
class CardButton:
    """One button. ``url`` opens a link; otherwise ``action`` becomes a ``cd:`` press."""

    text: str
    action: str | None = None
    url: str | None = None
    callback_data: str | None = None


@dataclass(frozen=True)
class Screen:
    """The content of one card, independent of where it is drawn.

    ``rows`` are the consumer's own buttons (above the framework's navigation row), so
    a plugin keeps buttons it already had — for example a mailroom ``mr:`` token button
    — without the card framework needing to understand them.
    """

    title: str
    body: str = ""
    kind: str = SUMMARY
    rows: tuple[tuple[Any, ...], ...] = ()
    open_label: str = "Open"
    open_action: str = "open"
    #: The rich projection of ``body``: headings, lists, tables, folds, timestamps
    #: and bare links the classic ``parse_mode=HTML`` cannot carry. ``body`` stays
    #: the plain twin, so a chat without rich messages still receives the card.
    #: ``None`` means "not rich": the card travels down the classic path only.
    rich_body: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in _KINDS:
            raise ValueError(f"unknown card kind {self.kind!r}; expected one of {sorted(_KINDS)}")

    def text(self) -> str:
        """Title and body as the card reads. Clamped to what Telegram will accept."""
        text = f"{self.title}\n\n{self.body}".strip() if self.body else str(self.title)
        if len(text) > TEXT_LIMIT:
            text = text[: TEXT_LIMIT - 20].rstrip() + "\n\n… (truncated)"
        return text

    def rich(self) -> str | None:
        """The rich rendering, or ``None`` when this card did not author one.

        The title becomes a heading here rather than a bold first line: a rich
        message has real outline levels, and the heading is what makes a long
        card scannable on a phone.
        """
        if self.rich_body is None:
            return None
        from navig.telegram.card_doc import esc  # noqa: PLC0415 — kit is optional

        head = f"<h2>{esc(str(self.title))}</h2>"
        return f"{head}\n{self.rich_body}".strip()


@dataclass(frozen=True)
class Context:
    """What a card is about: the owning prefix and the consumer's key (a draft/job id)."""

    prefix: str
    key: str


@dataclass(frozen=True)
class Action:
    """One press, or one pending text reply, offered to a registered handler."""

    prefix: str
    action: str
    key: str
    channel: Any = None
    chat_id: int | None = None
    message_id: int | None = None
    user_id: int | None = None
    callback_query: dict[str, Any] = field(default_factory=dict)
    text: str = ""


Handler = Callable[[Action], Screen | Awaitable[Screen | None] | None]


@dataclass(frozen=True)
class _Entry:
    handler: Handler
    module: str | None


@dataclass
class _PendingReply:
    prefix: str
    key: str
    chat_id: int
    message_id: int | None
    user_id: int | None
    action: str
    expires: float


_REGISTRY: dict[str, _Entry] = {}
_PENDING: dict[tuple[int, int | None], _PendingReply] = {}
_INFLIGHT: set[tuple[str, str, str]] = set()


# ── registry ────────────────────────────────────────────────────────────────


def register_card_action(prefix: str, handler: Handler, *, module: str | None = None) -> None:
    """Route ``cd:<prefix>:*`` presses to *handler*.

    *module* ties the card to a plugin's module switch: while it is off, every press is
    answered "switched off" and never reaches the handler (the same promise core's own
    extensions make). Re-registering replaces the handler, so a reloaded plugin can
    register again safely.
    """
    if not prefix or ":" in prefix or not prefix.replace("_", "").isalnum():
        raise ValueError(f"card prefix must be a short alphanumeric token — got {prefix!r}")
    if len(prefix) > 8:
        raise ValueError(f"card prefix {prefix!r} is too long (8 characters at most)")
    if not callable(handler):
        raise ValueError("a card action needs a callable handler")
    _REGISTRY[prefix] = _Entry(handler, module or None)


def unregister_card_action(prefix: str) -> None:
    _REGISTRY.pop(prefix, None)


def registered() -> dict[str, str]:
    """``{prefix: module}`` — for diagnostics and tests (module is "" when ungated)."""
    return {prefix: entry.module or "" for prefix, entry in _REGISTRY.items()}


# ── encoding ────────────────────────────────────────────────────────────────


def encode(prefix: str, action: str, key: str) -> str:
    """The ``callback_data`` for one card button. Raises before Telegram would."""
    if not prefix or not action:
        raise ValueError("a card button needs a prefix and an action")
    payload = f"{CALLBACK_PREFIX}{prefix}:{action}:{key}"
    if len(payload.encode("utf-8")) > 64:
        raise ValueError(
            f"card callback data is {len(payload.encode('utf-8'))} bytes (Telegram allows 64): "
            f"{payload!r} — use a shorter key or action"
        )
    return payload


def parse(callback_data: str) -> tuple[str, str, str] | None:
    """``"cd:dist:approve:draft-1"`` → ``("dist", "approve", "draft-1")``."""
    if not isinstance(callback_data, str) or not callback_data.startswith(CALLBACK_PREFIX):
        return None
    rest = callback_data[len(CALLBACK_PREFIX) :]
    prefix, sep, tail = rest.partition(":")
    if not sep or not prefix:
        return None
    action, sep, key = tail.partition(":")
    if not sep or not action:
        return None
    return prefix, action, key


# ── rendering ───────────────────────────────────────────────────────────────


def _nav_buttons(screen: Screen) -> list[CardButton]:
    if screen.kind == SUMMARY:
        return [
            CardButton(screen.open_label, action=screen.open_action),
            CardButton("Close", action="close"),
        ]
    if screen.kind == DETAIL:
        return [CardButton("Back", action="back"), CardButton("Close", action="close")]
    return [CardButton("Close", action="close")]


def _markup_button(item: Any, context: Context) -> dict[str, str] | None:
    if isinstance(item, Mapping):
        # A consumer's own row (an existing plugin button with a raw callback payload).
        text = str(item.get("text") or "").strip()
        if not text:
            return None
        if item.get("url"):
            return {"text": text, "url": str(item["url"])}
        if item.get("callback_data"):
            return {"text": text, "callback_data": str(item["callback_data"])}
        return None
    if not isinstance(item, CardButton):
        raise TypeError(f"card buttons must be CardButton or dict rows — got {type(item).__name__}")
    text = str(item.text or "").strip()
    if not text:
        return None
    if item.url:
        return {"text": text, "url": item.url}
    data = item.callback_data or encode(context.prefix, item.action or "", context.key)
    return {"text": text, "callback_data": data}


def render(screen: Screen, context: Context) -> tuple[str, list[list[dict[str, str]]]]:
    """``(text, inline_keyboard)`` for *screen* in *context*.

    The consumer rows come first (so a state-aware action stays where the operator
    expects it) and the framework's navigation row closes the card.
    """
    rows: list[list[dict[str, str]]] = []
    for row in screen.rows:
        built = [b for b in (_markup_button(item, context) for item in row) if b is not None]
        if built:
            rows.append(built)
    nav = [b for b in (_markup_button(item, context) for item in _nav_buttons(screen)) if b]
    if nav:
        rows.append(nav)
    return screen.text(), rows


def result_screen(title: str, body: str = "") -> Screen:
    """The "this happened" card: one message with a Close button."""
    return Screen(title=title, body=body, kind=RESULT)


# ── transport ───────────────────────────────────────────────────────────────


async def _api(channel: Any, method: str, payload: dict[str, Any]) -> Any:
    """Call the Bot API through *channel* when we have it, otherwise directly."""
    call = getattr(channel, "_api_call", None) if channel is not None else None
    if call is not None:
        return await call(method, payload)
    from navig.telegram.api import api_call, load_telegram_token

    token = load_telegram_token()
    return await asyncio.to_thread(api_call, token, method, payload)


def _keyboard(rows: Sequence[Sequence[Mapping[str, str]]]) -> dict[str, list]:
    return {"inline_keyboard": [list(row) for row in rows]}


def _sent(result: Any) -> dict[str, Any]:
    """The Bot API message object out of whichever envelope came back.

    ``api_call`` returns ``{"ok": true, "result": {...}}`` while the channel
    returns the message itself, so a caller reading ``message_id`` off the raw
    return only worked on one of the two paths — the CLI reported every card as
    sent with no id it could later edit or delete.
    """
    if not isinstance(result, Mapping):
        return {}
    inner = result.get("result")
    if "ok" in result and isinstance(inner, Mapping):
        return dict(inner)
    return dict(result)


async def _send_rich(
    channel: Any,
    chat_id: int | str,
    html: str,
    *,
    fallback: str,
    rows: Sequence[Sequence[Mapping[str, str]]],
) -> Any:
    """Send *html* as a rich message; ``None`` means "let the caller send classic".

    The fallback body always travels with the rich one. ``sendRichMessage`` is
    new enough that a Bot API server can answer "unknown method"; the channel then
    retries with ``sendMessage``, and reusing the rich HTML there would fail with a
    parse error (``<h2>``/``<table>`` are not classic entities) — a lost card
    instead of a layout downgrade.
    """
    list_rows = [list(row) for row in rows]
    send_rich = getattr(channel, "send_rich_message", None) if channel is not None else None
    if send_rich is not None:
        try:
            return await send_rich(chat_id, html=html, fallback_html=fallback, keyboard=list_rows)
        except Exception:  # noqa: BLE001 — the classic send below is the answer
            logger.debug("rich send refused; falling back to the classic body", exc_info=True)
            return None
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "rich_message": {"html": html},
        "reply_markup": _keyboard(rows),
    }
    try:
        return await _api(channel, "sendRichMessage", payload)
    except Exception as exc:  # noqa: BLE001 — an older server, or a body it rejects
        logger.info("sendRichMessage unavailable (%s) — sending the classic body", str(exc)[:120])
        return None


async def _edit_rich(
    channel: Any,
    chat_id: int | str,
    message_id: int,
    html: str,
    *,
    fallback: str,
    rows: Sequence[Sequence[Mapping[str, str]]],
) -> bool | None:
    """Edit in place keeping the rich body. ``None`` = the caller edits classic."""
    list_rows = [list(row) for row in rows]
    edit_rich = getattr(channel, "edit_rich_message", None) if channel is not None else None
    if edit_rich is not None:
        try:
            result = await edit_rich(
                chat_id, message_id, html=html, fallback_html=fallback, keyboard=list_rows
            )
        except Exception:  # noqa: BLE001 — "not modified" and refusals both land here
            logger.debug("rich edit refused; trying the classic body", exc_info=True)
            return None
        if result is not None:
            return True
        return None
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "rich_message": {"html": html},
        "reply_markup": _keyboard(rows),
    }
    try:
        result = await _api(channel, "editMessageText", payload)
    except Exception as exc:  # noqa: BLE001
        logger.info("rich edit unavailable (%s) — editing the classic body", str(exc)[:120])
        return None
    return bool(result)


async def answer(channel: Any, callback_query: dict[str, Any], text: str = "") -> None:
    """``answerCallbackQuery`` — always best effort; a stale id is not an error."""
    callback_id = (callback_query or {}).get("id")
    if not callback_id:
        return
    payload: dict[str, Any] = {"callback_query_id": callback_id}
    if text:
        payload["text"] = text[:190]
    try:
        await _api(channel, "answerCallbackQuery", payload)
    except Exception:  # noqa: BLE001 — stopping the spinner must never fail a press
        logger.debug("answerCallbackQuery failed", exc_info=True)


async def send_card(
    chat_id: int | str,
    screen: Screen,
    context: Context,
    *,
    channel: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    """Send *screen* as a new card. Returns the Bot API's message object (may be empty).

    One card, one delivery: a byte-identical card to the same chat inside the window
    is a retry or a second brain, not a new event. On 2026-10-22 a daily review card
    could be sent by more than one run/process and the operator received duplicates;
    the plugin's review command sends through this function, so the guard belongs
    here rather than in every caller.
    """
    text, rows = render(screen, context)
    if not force and screen.kind == SUMMARY:
        try:
            from navig.telegram import turn_state  # noqa: PLC0415

            if turn_state.in_flight(chat_id):
                turn_state.defer_summary(chat_id, screen, context)
                logger.info(
                    "digest card %r deferred — a turn is in flight for chat %s",
                    context.key,
                    chat_id,
                )
                return {}
        except Exception:  # noqa: BLE001 — a deferral bug must not block the card
            logger.debug("turn deferral skipped", exc_info=True)
    from navig.notify.dedupe import claim  # noqa: PLC0415 — cards work without notify installed

    fingerprint = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    key = f"card:{context.prefix}:{context.key}:{chat_id}:{fingerprint}"
    if not claim(key, CARD_DEDUPE_WINDOW_S):
        logger.info("duplicate card %r withheld for chat %s", context.key, chat_id)
        return {}
    if screen.kind == SUMMARY:
        day_key = f"cardday:{context.prefix}:{context.key}:{chat_id}:{_local_day()}"
        if not claim(day_key, CARD_DAILY_WINDOW_S):
            logger.info(
                "daily digest card %r already sent today to chat %s — withheld",
                context.key,
                chat_id,
            )
            return {}
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": _keyboard(rows),
        "disable_web_page_preview": True,
    }
    rich = screen.rich()
    if rich is not None:
        sent = await _send_rich(channel, chat_id, rich, fallback=text, rows=rows)
        if sent:
            return _sent(sent)
    result = await _api(channel, "sendMessage", payload)
    return _sent(result)


async def update_card(
    chat_id: int | str,
    message_id: int,
    screen: Screen,
    context: Context,
    *,
    channel: Any = None,
) -> bool:
    """Edit the card in place. False when Telegram had nothing to change (or refused)."""
    text, rows = render(screen, context)
    rich = screen.rich()
    if rich is not None:
        edited = await _edit_rich(channel, chat_id, message_id, rich, fallback=text, rows=rows)
        if edited is not None:
            return edited
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": _keyboard(rows),
        "disable_web_page_preview": True,
    }
    try:
        result = await _api(channel, "editMessageText", payload)
    except RuntimeError as exc:
        if "not modified" in str(exc).lower():
            return False
        raise
    return bool(result)


async def close_card(
    chat_id: int | str,
    message_id: int,
    *,
    channel: Any = None,
    text: str = CLOSED_TEXT,
) -> bool:
    """Collapse the card to one line and drop its keyboard."""
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "reply_markup": {"inline_keyboard": []},
    }
    try:
        result = await _api(channel, "editMessageText", payload)
    except RuntimeError as exc:
        if "not modified" in str(exc).lower():
            return False
        raise
    return bool(result)


# ── callback dispatch ───────────────────────────────────────────────────────


def _module_enabled(module: str) -> bool:
    """The plugin's module switch. An unreadable state counts as OFF, like core's gate."""
    try:
        from navig.modules.registry import get_registry

        return bool(get_registry().is_enabled(module))
    except Exception:  # noqa: BLE001 — never hand a press to a plugin we cannot see
        logger.debug("module state for %s unreadable — treating as off", module, exc_info=True)
        return False


def _ids(callback_query: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    sender = callback_query.get("from") or {}
    return chat.get("id"), message.get("message_id"), sender.get("id")


async def _invoke(entry: _Entry, action: Action) -> Screen | None:
    result = entry.handler(action)
    if inspect.isawaitable(result):
        result = await result
    if result is not None and not isinstance(result, Screen):
        raise TypeError(f"card handler returned {type(result).__name__}; expected Screen or None")
    return result


async def dispatch(channel: Any, callback_query: dict[str, Any], callback_data: str) -> bool:
    """Handle one ``cd:`` press. True when this module owned it.

    Answers the callback **before** running the handler (the spinner stops at once),
    then edits the card in place with whatever screen the handler returns. Unknown
    prefixes and expired keys answer a short toast instead of failing silently.
    """
    parsed = parse(callback_data)
    if parsed is None:
        if isinstance(callback_data, str) and callback_data.startswith(CALLBACK_PREFIX):
            # A truncated/old payload is still OUR button: answer it, or the spinner
            # spins for 30 s on a press the channel has already claimed.
            await answer(channel, callback_query, "That button is no longer valid.")
            return True
        return False
    prefix, action_name, key = parsed
    chat_id, message_id, user_id = _ids(callback_query)

    entry = _REGISTRY.get(prefix)
    if entry is None:
        await answer(channel, callback_query, "This card is no longer available.")
        return True
    if entry.module and not _module_enabled(entry.module):
        await answer(channel, callback_query, f"{entry.module} is switched off in NAVIG.")
        return True

    # Any press on a card supersedes a pending "reply with the reason" prompt.
    _clear_pending(chat_id, user_id)

    if action_name == "close":
        await answer(channel, callback_query, "")
        if chat_id is not None and message_id is not None:
            await _close_safely(chat_id, message_id, channel=channel)
        return True

    flight = (prefix, action_name, key)
    if flight in _INFLIGHT:
        await answer(channel, callback_query, "Already working on that…")
        return True
    _INFLIGHT.add(flight)
    await answer(channel, callback_query, "")
    action = Action(
        prefix=prefix,
        action=action_name,
        key=key,
        channel=channel,
        chat_id=chat_id,
        message_id=message_id,
        user_id=user_id,
        callback_query=dict(callback_query),
    )
    try:
        screen = await _invoke(entry, action)
    except Exception:  # noqa: BLE001 — a plugin bug must not leave a dead card
        logger.warning("card action %s:%s failed", prefix, action_name, exc_info=True)
        screen = result_screen("That did not work", "See the NAVIG log for the detail.")
    finally:
        _INFLIGHT.discard(flight)

    if screen is not None and chat_id is not None and message_id is not None:
        await _update_safely(chat_id, message_id, screen, Context(prefix, key), channel=channel)
    return True


async def _update_safely(
    chat_id: int, message_id: int, screen: Screen, context: Context, *, channel: Any
) -> bool:
    try:
        return await update_card(chat_id, message_id, screen, context, channel=channel)
    except Exception:  # noqa: BLE001 — the outcome stands even if the edit did not land
        logger.warning("could not update card %s/%s", chat_id, message_id, exc_info=True)
        return False


async def _close_safely(chat_id: int, message_id: int, *, channel: Any) -> bool:
    try:
        return await close_card(chat_id, message_id, channel=channel)
    except Exception:  # noqa: BLE001
        logger.warning("could not close card %s/%s", chat_id, message_id, exc_info=True)
        return False


# ── pending text replies (Reject asks for a reason) ─────────────────────────


def _clear_pending(chat_id: int | None, user_id: int | None) -> None:
    if chat_id is not None:
        _PENDING.pop((int(chat_id), user_id), None)


def await_text_reply(
    action: Action,
    action_name: str = "reason",
    *,
    ttl_s: int = REPLY_TTL_S,
) -> None:
    """The next text message in this chat becomes ``action_name`` on the same card.

    A button cannot ask for a reason, so the Reject button edits the card to a prompt
    and this registers what its next reply means. The prompt is dropped by any later
    card press, by a slash command, or when it expires.
    """
    if action.chat_id is None:
        raise ValueError("a pending reply needs a chat id")
    _PENDING[(int(action.chat_id), action.user_id)] = _PendingReply(
        prefix=action.prefix,
        key=action.key,
        chat_id=int(action.chat_id),
        message_id=action.message_id,
        user_id=action.user_id,
        action=action_name,
        expires=time.monotonic() + max(30, int(ttl_s)),
    )


def clear_text_reply(chat_id: int | None, user_id: int | None) -> None:
    _clear_pending(chat_id, user_id)


def has_pending_reply(chat_id: int, user_id: int | None) -> bool:
    pending = _PENDING.get((int(chat_id), user_id))
    return pending is not None and pending.expires >= time.monotonic()


async def consume_reply(channel: Any, message: dict[str, Any]) -> bool:
    """Take the next plain-text message as a pending card reply. True when consumed.

    Called by the channel **after** its own access control, so only an authorised
    sender's message can ever become a reason. A slash command cancels the prompt and
    falls through to normal dispatch — typing ``/status`` must not become a rejection.
    """
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = sender.get("id")
    text = message.get("text")
    if chat_id is None or not isinstance(text, str) or not text.strip():
        return False
    pending = _PENDING.get((int(chat_id), user_id))
    if pending is None:
        return False
    if pending.expires < time.monotonic():
        _PENDING.pop((int(chat_id), user_id), None)
        return False
    if text.lstrip().startswith("/"):
        _PENDING.pop((int(chat_id), user_id), None)
        return False
    _PENDING.pop((int(chat_id), user_id), None)

    entry = _REGISTRY.get(pending.prefix)
    if entry is None:
        return False
    if entry.module and not _module_enabled(entry.module):
        return False
    action = Action(
        prefix=pending.prefix,
        action=pending.action,
        key=pending.key,
        channel=channel,
        chat_id=int(chat_id),
        message_id=pending.message_id,
        user_id=user_id,
        text=text.strip(),
    )
    try:
        screen = await _invoke(entry, action)
    except Exception:  # noqa: BLE001
        logger.warning("card reply %s:%s failed", pending.prefix, pending.action, exc_info=True)
        screen = result_screen("That did not work", "See the NAVIG log for the detail.")
    if screen is not None and pending.message_id is not None:
        await _update_safely(
            int(chat_id),
            pending.message_id,
            screen,
            Context(pending.prefix, pending.key),
            channel=channel,
        )
    return True


__all__ = [
    "Action",
    "CardButton",
    "CALLBACK_PREFIX",
    "CLOSED_TEXT",
    "Context",
    "DETAIL",
    "RESULT",
    "SUMMARY",
    "Screen",
    "answer",
    "await_text_reply",
    "clear_text_reply",
    "close_card",
    "consume_reply",
    "dispatch",
    "encode",
    "has_pending_reply",
    "parse",
    "register_card_action",
    "registered",
    "render",
    "result_screen",
    "send_card",
    "unregister_card_action",
    "update_card",
]
