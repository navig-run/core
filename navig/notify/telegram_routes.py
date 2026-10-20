"""Per-type Telegram routing — send a notification type to its own chat.

Every Telegram notification used to land in the operator's one DM. That makes a
distinct sound impossible: Telegram picks the sound **per chat** on the
operator's phone, and a bot cannot choose it. So a type can now be routed to its
own chat — ``sale`` → a private "Money" channel with a cash-register sound,
``reminder`` → a "Reminders" channel with a chime — and everything else keeps
going to the DM.

Two knobs per type, both stored in ``notify_settings``:

* ``tg_route:<type>`` — JSON ``{"chat_id": int, "title": str}``. Unset = the DM.
* ``tg_silent:<type>`` — ``"1"``/``"0"``: send with ``disable_notification`` so
  the message lands without a sound. Unset = the type's ``silent_default``
  (only ``sales_digest`` today: a daily summary must never ring like a sale, or
  the ring stops meaning anything).

A routed chat can break without anyone noticing: the bot gets removed, the
channel is deleted, a group is upgraded to a supergroup (which changes its id).
A sale must never vanish because of that, so :func:`deliver`:

1. follows a supergroup migration (``migrate_to_chat_id``) — updates the route
   and retries, no human needed;
2. otherwise falls back to the DM with a one-line note saying where the message
   was meant to go and why it is here instead;
3. logs a WARNING and records the failure in the deck feed (at most once an
   hour per chat), so the broken route is visible, not just survivable.

Chat resolution (:func:`resolve_chat`) accepts what a person actually has in
hand: the id the Telegram app shows for a channel (``4466964318``), the Bot API
form (``-1004466964318``), or ``@username`` — and checks the bot can post there
*before* the route is saved, so a typo fails at setup instead of at the first
sale.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("navig.notify.telegram_routes")

_ROUTE_PREFIX = "tg_route:"
_SILENT_PREFIX = "tg_silent:"

#: A broken route is recorded in the deck feed at most this often per chat.
_FAILURE_NOTE_EVERY_SEC = 3600
_last_failure_note: dict[int, float] = {}

_TIMEOUT = 15


# ── storage ──────────────────────────────────────────────────────────────────


def _conn():
    from navig.notify import store

    store.init_db()
    return store.conn()


def _get(key: str) -> str | None:
    """Read one setting. An unreadable store reads as "unset" — the DM and the
    type's default sound — because a routing lookup must never cost a delivery."""
    try:
        r = _conn().execute("SELECT value FROM notify_settings WHERE key = ?", (key,)).fetchone()
    except Exception as exc:  # noqa: BLE001
        logger.warning("telegram route read failed (%s) — using the DM / default sound", exc)
        return None
    return r["value"] if r else None


def _put(key: str, value: str | None) -> None:
    c = _conn()
    with c:
        if value is None:
            c.execute("DELETE FROM notify_settings WHERE key = ?", (key,))
        else:
            c.execute(
                "INSERT INTO notify_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


def _check_type(type_key: str) -> None:
    from navig.notify import prefs

    if not prefs._is_known_type(type_key):
        raise ValueError(f"unknown notification type: {type_key}")


@dataclass(frozen=True)
class Route:
    chat_id: int
    title: str = ""

    def label(self) -> str:
        return self.title or str(self.chat_id)


def get_route(type_key: str) -> Route | None:
    raw = _get(_ROUTE_PREFIX + type_key)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Route(chat_id=int(data["chat_id"]), title=str(data.get("title") or ""))
    except (ValueError, KeyError, TypeError):
        # A corrupt row must not cost the delivery: treat it as "no route" (the DM).
        logger.warning("telegram route for %r is unreadable (%r) — using the DM", type_key, raw)
        return None


def set_route(type_key: str, chat_id: int | None, title: str = "") -> None:
    """Route *type_key* to *chat_id*; ``None`` clears it (back to the DM)."""
    _check_type(type_key)
    if chat_id is None:
        _put(_ROUTE_PREFIX + type_key, None)
        return
    _put(_ROUTE_PREFIX + type_key, json.dumps({"chat_id": int(chat_id), "title": title}))


def silent_default(type_key: str) -> bool:
    from navig.notify.types import NOTIFICATION_TYPES

    return any(t["key"] == type_key and t.get("silent_default") for t in NOTIFICATION_TYPES)


def is_silent(type_key: str) -> bool:
    raw = _get(_SILENT_PREFIX + type_key)
    if raw is None or raw == "":
        return silent_default(type_key)
    from navig.core.coerce import coerce_bool

    return coerce_bool(raw, default=silent_default(type_key))


def set_silent(type_key: str, silent: bool | None) -> None:
    """Set whether *type_key* lands without a sound; ``None`` resets to the default."""
    _check_type(type_key)
    _put(_SILENT_PREFIX + type_key, None if silent is None else ("1" if silent else "0"))


def all_routes() -> dict[str, dict[str, Any]]:
    """``{type: {chat_id, title, silent}}`` for every registered type (UI + CLI)."""
    from navig.notify.types import TYPE_KEYS

    out: dict[str, dict[str, Any]] = {}
    for t in TYPE_KEYS:
        r = get_route(t)
        out[t] = {
            "chat_id": r.chat_id if r else None,
            "title": r.title if r else "",
            "silent": is_silent(t),
            "silent_default": silent_default(t),
        }
    return out


# ── Bot API ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SendOutcome:
    ok: bool
    error: str = ""
    migrate_to: int | None = None


def _token() -> str:
    from navig.messaging.secrets import resolve_telegram_bot_token

    token = resolve_telegram_bot_token()
    if not token:
        raise RuntimeError("Telegram bot token missing. Configure with: navig init")
    return token


def _call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST to the Bot API and return the JSON body — on a 4xx too.

    ``commands.telegram._api_call`` raises on the HTTP status before reading the
    body, which throws away exactly what this module needs: Telegram's
    ``description`` and ``parameters.migrate_to_chat_id``.
    """
    import requests

    resp = requests.post(
        f"https://api.telegram.org/bot{_token()}/{method}", json=payload, timeout=_TIMEOUT
    )
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "description": f"HTTP {resp.status_code}"}
    return data if isinstance(data, dict) else {"ok": False, "description": "bad response"}


def send(
    chat_id: int | str,
    text: str,
    *,
    silent: bool = False,
    parse_mode: str = "HTML",
    keyboard: list[list[dict]] | None = None,
) -> SendOutcome:
    """One ``sendMessage``. Never raises — the outcome carries the error."""
    from navig.messaging.notify_operator import truncate_for_telegram

    payload: dict[str, Any] = {"chat_id": chat_id, "text": truncate_for_telegram(text)}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if silent:
        payload["disable_notification"] = True
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    try:
        data = _call("sendMessage", payload)
    except Exception as exc:  # noqa: BLE001 — network/token problems are an outcome, not a crash
        return SendOutcome(False, str(exc))
    if data.get("ok"):
        try:
            from navig.telegram import chat_tidy

            chat_tidy.note(data.get("result"))
        except Exception:  # noqa: BLE001 — the tidy ledger is never worth a delivery
            logger.debug("chat_tidy.note failed", exc_info=True)
        return SendOutcome(True)
    params = data.get("parameters") or {}
    migrate = params.get("migrate_to_chat_id")
    return SendOutcome(
        False,
        str(data.get("description") or "Telegram refused the message"),
        int(migrate) if migrate else None,
    )


@dataclass(frozen=True)
class ResolvedChat:
    chat_id: int
    title: str
    kind: str  # channel | supergroup | group | private


def resolve_chat(raw: str) -> ResolvedChat:
    """Turn what the operator pasted into a chat the bot can post in.

    Raises ``ValueError`` with a message fit to show a person.
    """
    text = (raw or "").strip()
    if text.startswith("https://t.me/") or text.startswith("t.me/"):
        tail = text.split("t.me/", 1)[1].strip("/")
        if tail.startswith("+") or tail.startswith("joinchat/"):
            # A private invite link carries no chat id, and a bot cannot join by link.
            raise ValueError(
                "An invite link doesn't identify the chat for a bot. Add the bot to the "
                "channel as an admin, then paste the channel id (e.g. 4466964318)."
            )
        text = "@" + tail
    if not text:
        raise ValueError("Paste a chat id or @username.")

    if text.startswith("@"):
        candidates: list[str | int] = [text]
    elif text.lstrip("-").isdigit():
        n = text.lstrip("-")
        if text.startswith("-100"):
            candidates = [int(text)]
        elif text.startswith("-"):
            candidates = [int(text), int(f"-100{n}")]
        else:
            # The Telegram apps show a channel's id without the Bot API "-100" prefix.
            candidates = [int(f"-100{n}"), int(f"-{n}"), int(n)]
    else:
        raise ValueError("A chat id is a number (e.g. 4466964318) or an @username.")

    last_error = "chat not found"
    for cand in candidates:
        try:
            data = _call("getChat", {"chat_id": cand})
        except Exception as exc:  # noqa: BLE001
            raise ValueError(f"Couldn't reach Telegram: {exc}") from exc
        if not data.get("ok"):
            last_error = str(data.get("description") or last_error)
            continue
        chat = data["result"]
        resolved = ResolvedChat(
            chat_id=int(chat["id"]),
            title=str(chat.get("title") or chat.get("username") or chat.get("first_name") or ""),
            kind=str(chat.get("type") or ""),
        )
        _check_can_post(resolved)
        return resolved
    raise ValueError(
        f"The bot can't see that chat ({last_error}). Add the bot to it first — "
        "as an admin with 'Post messages' for a channel."
    )


def _check_can_post(chat: ResolvedChat) -> None:
    if chat.kind == "private":
        return
    try:
        me = _call("getMe", {}).get("result") or {}
        member = _call("getChatMember", {"chat_id": chat.chat_id, "user_id": me.get("id")})
    except Exception:  # noqa: BLE001 — the getChat succeeded; don't fail setup on a probe
        logger.debug("getChatMember probe failed", exc_info=True)
        return
    m = member.get("result") or {}
    status = m.get("status")
    if status in ("left", "kicked"):
        raise ValueError(f"The bot isn't a member of “{chat.title}”. Add it, then try again.")
    if chat.kind == "channel" and not (status == "creator" or m.get("can_post_messages")):
        raise ValueError(
            f"The bot can't post in “{chat.title}”. Make it an admin with 'Post messages'."
        )


# ── delivery ─────────────────────────────────────────────────────────────────


def _record_route_failure(type_key: str, route: Route, error: str) -> None:
    logger.warning(
        "telegram route for %r → %s (%s) failed: %s — delivered to the DM instead",
        type_key, route.label(), route.chat_id, error,
    )
    now = time.monotonic()
    last = _last_failure_note.get(route.chat_id)
    if last is not None and now - last < _FAILURE_NOTE_EVERY_SEC:
        return
    _last_failure_note[route.chat_id] = now
    try:
        from navig.notify import feed

        feed.append(
            "system_alert",
            f"Telegram chat “{route.label()}” isn't accepting notifications",
            f"{type_key} notifications are going to your DM instead. Telegram said: {error}. "
            "Check the bot is still an admin there, or change the chat in "
            "Notifications → Telegram chats.",
            priority="high",
            data={"type": type_key, "chat_id": route.chat_id},
        )
    except Exception:  # noqa: BLE001 — recording the failure must not cost the delivery
        logger.debug("route-failure feed note failed", exc_info=True)


def _dm(text: str, chat_id: str | None, *, silent: bool, keyboard: list[list[dict]] | None = None) -> bool:
    """The DM send — through ``notify_operator``, the one process-agnostic DM path."""
    from navig.messaging import notify_operator as no

    if keyboard:
        return bool(no.notify_operator(text, chat_id=chat_id, silent=silent, keyboard=keyboard))
    return bool(no.notify_operator(text, chat_id=chat_id, silent=silent))


def deliver(
    type_key: str,
    text: str,
    *,
    fallback_chat_id: str | None = None,
    keyboard: list[list[dict]] | None = None,
) -> tuple[bool, str]:
    """Send *text* (Telegram HTML) for *type_key*: its route, else the DM.

    *fallback_chat_id* overrides the DM target; ``None`` means the operator's own
    chat as ``notify_operator`` resolves it. Returns ``(ok, detail)``. ``ok`` is
    True when the message reached *some* chat — a routed send that fell back to
    the DM still delivered, and the detail says so.
    """
    from html import escape

    silent = is_silent(type_key)
    route = get_route(type_key)

    if route is None:
        ok = _dm(text, fallback_chat_id, silent=silent, keyboard=keyboard)
        return ok, ("sent" if ok else "telegram not configured")

    out = send(route.chat_id, text, silent=silent, keyboard=keyboard)
    if not out.ok and out.migrate_to:
        # A group upgraded to a supergroup gets a new id. Follow it, keep the route.
        logger.info("telegram route for %r migrated %s → %s", type_key, route.chat_id, out.migrate_to)
        route = Route(chat_id=out.migrate_to, title=route.title)
        set_route(type_key, route.chat_id, route.title)
        out = send(route.chat_id, text, silent=silent, keyboard=keyboard)
    if out.ok:
        return True, f"sent to {route.label()}"

    _record_route_failure(type_key, route, out.error)
    note = (
        f"\n\n<i>⚠️ Meant for “{escape(route.label())}”, but Telegram refused it "
        f"({escape(out.error)}) — so it's here.</i>"
    )
    # The fallback is NOT silent: a broken route is exactly when you need to hear it.
    if _dm(text + note, fallback_chat_id, silent=False, keyboard=keyboard):
        return True, f"{route.label()} failed ({out.error}); delivered to your DM"
    return False, f"{route.label()}: {out.error}; the DM fallback failed too"


def default_chat_id() -> str | None:
    """The Telegram target setting (Notifications → Channels), or ``None`` for the DM."""
    try:
        from navig.notify import prefs

        return prefs.get_target("telegram").strip() or None
    except Exception:  # noqa: BLE001
        logger.debug("telegram target read failed", exc_info=True)
        return None


def wants_direct_delivery(type_key: str) -> bool:
    """True when the type needs this module's path (a route or a sound setting)."""
    try:
        return get_route(type_key) is not None or is_silent(type_key)
    except Exception:  # noqa: BLE001 — a store hiccup falls back to the old path
        logger.debug("telegram route read failed", exc_info=True)
        return False
