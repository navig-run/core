"""A shared to-do list inside a Telegram Business chat.

A Business chat is always two people: the owner (the operator, writing through their
own account) and one contact. Both can add and tick items by typing; only the owner
can delete or clear. Each chat has its own list (``board_list`` id ``tg:<chat_id>``),
stored in the Planner like every other list — so NAVIG OS and the Mini App show it too,
and a dated item pings THIS chat when it is due.

Typed, never tapped: Telegram does not route a contact's button presses to the bot
(`_is_user_authorized` drops them), so the card has no buttons and every action is a
word anyone in the chat can type:

    todo <text>            add      (also /задача, /tâche — those need the / or !)
    todo                   show the list   (also: список · liste · list)
    done 2  · done 1,3     tick     (готово · сделано · fait)
    undo 2                 untick   (вернуть · rouvrir)
    del 2                  delete   — owner only (удалить · suppr)
    clear                  remove ticked items — owner only (очистить · vider)
    clear all              empty the list — owner only

The card is re-posted after every change and the previous one deleted, so the chat
holds one current list instead of a trail of stale ones.
"""

from __future__ import annotations

import html
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any

logger = logging.getLogger(__name__)

#: Writes per chat per minute. A shared list is two people typing, not a script.
RATE_LIMIT = 20
_WRITES: dict[int, deque[float]] = defaultdict(deque)

_NUMBERS_RE = re.compile(r"^\s*#?\d+(?:[\s,]+#?\d+)*\s*$")
_COPULA = frozenset(
    "is was are were be will это было будет est était sera c'est".split()
)

_COPY: dict[str, dict[str, str]] = {
    "en": {
        "title": "📋 <b>To-do</b> · {open} open",
        "empty": "<i>Nothing yet. Add one: <code>todo buy paper friday</code></i>",
        "hint": "<i>todo &lt;text&gt; · done N · undo N</i>",
        "owner": "the owner",
        "added_by": "added by {name}",
        "more": "<i>… and {n} more</i>",
        "owner_only": "Only {owner} can delete items from this list.",
        "not_found": "There is no item #{n} in this list.",
        "full": "This list is full — tick or clear some items first.",
        "slow": "Too many changes at once — try again in a minute.",
        "off": "The shared list is switched off.",
    },
    "ru": {
        "title": "📋 <b>Задачи</b> · открыто: {open}",
        "empty": "<i>Пока пусто. Добавить: <code>todo купить бумагу в пятницу</code></i>",
        "hint": "<i>todo &lt;текст&gt; · готово N · вернуть N</i>",
        "owner": "владелец чата",
        "added_by": "добавил(а) {name}",
        "more": "<i>… и ещё {n}</i>",
        "owner_only": "Удалять задачи из списка может только {owner}.",
        "not_found": "В списке нет задачи #{n}.",
        "full": "Список заполнен — отметьте или очистите выполненные.",
        "slow": "Слишком много изменений сразу — попробуйте через минуту.",
        "off": "Общий список выключен.",
    },
    "fr": {
        "title": "📋 <b>À faire</b> · {open} ouverte(s)",
        "empty": "<i>Rien pour l'instant. Ajouter : <code>todo acheter du papier vendredi</code></i>",
        "hint": "<i>todo &lt;texte&gt; · fait N · rouvrir N</i>",
        "owner": "le propriétaire",
        "added_by": "ajouté par {name}",
        "more": "<i>… et {n} de plus</i>",
        "owner_only": "Seul·e {owner} peut supprimer des éléments de cette liste.",
        "not_found": "Il n'y a pas d'élément n°{n} dans cette liste.",
        "full": "Cette liste est pleine — cochez ou videz des éléments d'abord.",
        "slow": "Trop de changements d'un coup — réessayez dans une minute.",
        "off": "La liste partagée est désactivée.",
    },
}

#: Words → action. "add" words with no text show the list.
ADD_WORDS = ("todo", "to-do", "туду", "задача", "tâche", "tache", "àfaire")
LIST_WORDS = ("list", "todos", "список", "задачи", "liste", "tâches", "taches")
DONE_WORDS = ("done", "готово", "сделано", "fait", "fini")
UNDO_WORDS = ("undo", "вернуть", "rouvrir")
DEL_WORDS = ("del", "delete", "удалить", "suppr", "supprimer")
CLEAR_WORDS = ("clear", "очистить", "vider")
#: Words common enough in ordinary speech that only an explicit "/" or "!" may
#: trigger them: "Задача сложная" is a sentence, not a task.
EXPLICIT_ONLY = frozenset({"задача", "tâche", "tache", "задачи", "tâches", "taches", "list", "liste", "fini"})
_ALL = frozenset({"all", "всё", "все", "tout"})


# ── bare-word rules (used by biz_commands._BARE_OK) ──────────────────────────


def bare_ok(word: str, arg: str, text: str) -> bool:
    """Is this message, typed WITHOUT "/" or "!", really a list command?"""
    word = (word or "").lower()
    if word in EXPLICIT_ONLY:
        return False
    stripped = arg.strip()
    if word in ADD_WORDS:
        if not stripped:
            return True  # "todo" alone shows the list
        first = stripped.split()[0].lower().strip(":,")
        return first not in _COPULA and not stripped.endswith("?")
    if word in LIST_WORDS:
        return not stripped
    if word in DONE_WORDS or word in UNDO_WORDS or word in DEL_WORDS:
        return bool(_NUMBERS_RE.match(stripped))
    if word in CLEAR_WORDS:
        return not stripped or stripped.lower() in _ALL
    return False


def _numbers(arg: str) -> list[int]:
    return [int(n) for n in re.findall(r"\d+", arg or "")]


def _rate_ok(chat_id: int) -> bool:
    now = time.monotonic()
    window = _WRITES[chat_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        return False
    window.append(now)
    return True


def _enabled() -> bool:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415
        from navig.core.coerce import coerce_bool  # noqa: PLC0415

        raw = get_config_manager().get("telegram.business.shared_todo", True)
        return coerce_bool(raw, default=True)
    except Exception:  # noqa: BLE001
        return True


def _person(msg: dict[str, Any] | None) -> tuple[int | None, str]:
    sender = (msg or {}).get("from") or {}
    name = " ".join(x for x in (sender.get("first_name"), sender.get("last_name")) if x)
    return sender.get("id"), name or sender.get("username") or ""


def _peer_name(msg: dict[str, Any] | None) -> str:
    chat = (msg or {}).get("chat") or {}
    name = " ".join(x for x in (chat.get("first_name"), chat.get("last_name")) if x)
    return name or chat.get("username") or chat.get("title") or ""


# ── the card ─────────────────────────────────────────────────────────────────


#: Telegram's message limit, in UTF-16 units. The card is sent with a raw sendMessage
#: (no splitting), so a card over it is refused and the previous one stays, stale.
_CARD_MAX = 4096


def render(list_id: str, lang: str, *, owner_id: int | None = None, peer_name: str = "") -> str:
    """The list as one message. An item the CONTACT added says so — a Business chat
    has exactly two people, so "not the owner" is the contact by name. Open items that
    would push the card past Telegram's limit are summarised as "… and N more"."""
    from navig.gateway.channels.base import utf16_len  # noqa: PLC0415
    from navig.pim import service  # noqa: PLC0415

    copy = _COPY.get(lang) or _COPY["en"]
    items = service.tasks(list_id=list_id, include_done=True)
    open_items = [t for t in items if not t["done"]]
    done_items = [t for t in items if t["done"]][-5:]
    lines = [copy["title"].format(open=len(open_items)), ""]
    if not items:
        lines.append(copy["empty"])
    tail = [f"<code>#{t['list_seq']}</code> ☑ <s>{html.escape(t['title'])}</s>" for t in done_items]
    tail += ["", copy["hint"]]
    used = utf16_len("\n".join(lines + tail)) + utf16_len(copy["more"].format(n=len(items))) + 1
    shown = 0
    for t in open_items:
        line = f"<code>#{t['list_seq']}</code> ☐ {html.escape(t['title'])}"
        if t.get("when_label"):
            line += f" · <i>{html.escape(t['when_label'])}</i>"
        if peer_name and t.get("created_by") and owner_id and int(t["created_by"]) != int(owner_id):
            line += f" <i>({copy['added_by'].format(name=html.escape(peer_name))})</i>"
        if used + utf16_len(line) + 1 > _CARD_MAX:
            break
        used += utf16_len(line) + 1
        lines.append(line)
        shown += 1
    if shown < len(open_items):
        lines.append(copy["more"].format(n=len(open_items) - shown))
    return "\n".join(lines + tail)


def _board_url(channel: Any) -> str:
    """The Mini App board link, when this install has a deck URL."""
    try:
        return str(channel._get_deck_url() or "")
    except Exception:  # noqa: BLE001 — a missing deck must not stop the list
        return ""


async def _post_card(channel: Any, chat_id: int, bcid: str | None, lst: dict[str, Any]) -> None:
    """Post the current list and delete the previous one (best-effort).

    A rich card when the chat can show one: the list is a table, and the only buttons are
    a copy button and (when a deck exists) a link to the board — a Business chat's other
    person has no working callback buttons, so the card must not offer any.
    """
    from navig.pim import service  # noqa: PLC0415
    from navig.telegram import biz_card, biz_commands, card_transport  # noqa: PLC0415

    new_id: int | None = None
    try:
        items = service.tasks(list_id=lst["id"], include_done=True)
        card = biz_card.todo_card(
            items=items,
            peer_name=lst.get("peer_name") or "",
            owner_id=lst.get("owner_id"),
            board_url=_board_url(channel),
        )
        sent = await card_transport.send_card(
            channel, chat_id, card, business_connection_id=bcid
        )
        new_id = (sent or {}).get("message_id")
    except Exception:  # noqa: BLE001 — the text card below is the fallback, not a crash
        logger.debug("biz_todo card send failed, falling back to text", exc_info=True)
        new_id = None

    if not new_id:
        text = render(
            lst["id"], lst.get("lang") or "en",
            owner_id=lst.get("owner_id"), peer_name=lst.get("peer_name") or "",
        )
        new_id = await biz_commands._send(channel, chat_id, bcid, text)

    old_id = lst.get("last_card_msg_id")
    if new_id:
        service.update_list(lst["id"], {"last_card_msg_id": int(new_id)})
    if old_id and old_id != new_id:
        await biz_commands._delete(channel, chat_id, bcid, int(old_id))


async def _reply(channel: Any, chat_id: int, bcid: str | None, text: str) -> None:
    from navig.telegram import biz_commands  # noqa: PLC0415

    await biz_commands._send(channel, chat_id, bcid, html.escape(text))


# ── the command ──────────────────────────────────────────────────────────────


async def command(
    channel: Any,
    chat_id: int,
    bcid: str | None,
    arg: str,
    *,
    is_owner: bool,
    owner_id: int | None,
    trigger: dict[str, Any] | None = None,
    word: str = "todo",
    **_: Any,
) -> None:
    """One list command from a Business chat. Never raises into the dispatcher."""
    from navig.pim import service  # noqa: PLC0415
    from navig.telegram.biz_reminders import language  # noqa: PLC0415

    raw_text = ((trigger or {}).get("text") or "").strip().lstrip("/!").strip()
    raw_arg = raw_text.split(None, 1)[1] if len(raw_text.split(None, 1)) > 1 else ""
    lst = service.ensure_biz_list(
        int(chat_id),
        bcid=bcid,
        owner_id=owner_id,
        peer_name=_peer_name(trigger),
        lang=language(raw_text),
    )
    lang = lst.get("lang") or "en"
    copy = _COPY.get(lang) or _COPY["en"]
    if not _enabled():
        await _reply(channel, chat_id, bcid, copy["off"])
        return

    word = (word or "").lower()
    try:
        if word in ADD_WORDS and raw_arg.strip():
            if not _rate_ok(int(chat_id)):
                await _reply(channel, chat_id, bcid, copy["slow"])
                return
            who, _name = _person(trigger)
            service.create_task(
                raw_arg,
                list_id=lst["id"],
                created_by=int(who) if who else None,
                origin="chat",
            )
        elif word in DONE_WORDS or word in UNDO_WORDS:
            if not _rate_ok(int(chat_id)):
                await _reply(channel, chat_id, bcid, copy["slow"])
                return
            for n in _numbers(arg):
                item = service.by_seq(lst["id"], n)
                if item is None:
                    await _reply(channel, chat_id, bcid, copy["not_found"].format(n=n))
                    continue
                (service.complete if word in DONE_WORDS else service.reopen)(item["id"])
        elif word in DEL_WORDS:
            if not is_owner:
                await _reply(channel, chat_id, bcid, copy["owner_only"].format(owner=copy["owner"]))
                return
            for n in _numbers(arg):
                item = service.by_seq(lst["id"], n)
                if item is None:
                    await _reply(channel, chat_id, bcid, copy["not_found"].format(n=n))
                    continue
                service.delete(item["id"], actor_is_owner=True)
        elif word in CLEAR_WORDS:
            if not is_owner:
                await _reply(channel, chat_id, bcid, copy["owner_only"].format(owner=copy["owner"]))
                return
            if arg.strip().lower() in _ALL:
                for t in service.tasks(list_id=lst["id"], include_done=True):
                    service.delete(t["id"], actor_is_owner=True)
            else:
                service.clear_done(lst["id"])
        # Any other word (list words, a bare "todo") just shows the list.
    except service.PlannerError as exc:
        message = str(exc)
        if "already has" in message:
            message = copy["full"]
        await _reply(channel, chat_id, bcid, message)
        return
    except Exception:  # noqa: BLE001
        logger.exception("business todo %r failed", word)
        return
    await _post_card(channel, chat_id, bcid, service._store().get_list(lst["id"]) or lst)

