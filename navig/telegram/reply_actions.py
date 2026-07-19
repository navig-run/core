"""navig.telegram.reply_actions — reply-to-message keyword actions.

The portable action trigger that REPLACED Telegram emoji reactions. Reply to a
message with a BARE keyword → run that action on the replied-to message.

Why replies (not reactions): Telegram never delivers reactions for business-
account conversations, and a bot cannot attach callback buttons to a business
message nor edit messages authored by others — so reactions could never drive
actions there. (In fact the old reaction mixin was never even bound to the
channel, so reactions did nothing in ANY chat.) A reply is the one mechanism that
works everywhere — DM, group, and business.

Security: only the SANDBOXED no-tools LLM ops (translate/summarize/explain/
context) plus owner-local helpers (save/refine/pin) are reachable — a reply can
never touch the shell. In business chats the set is further restricted to
``BUSINESS_ACTIONS`` and every result is DM'd to the owner PRIVATELY, never into
the conversation. Non-LLM actions are owner-gated; LLM ops self-gate via the
per-tool policy in :mod:`navig.telegram.permissions`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

from navig.telegram import ai_actions

logger = logging.getLogger(__name__)

# keyword / alias → canonical action. Strict, single-word triggers only.
KEYWORDS: dict[str, str] = {
    # ── sandboxed no-tools LLM text ops (text-in → text-out; safe in business) ──
    "translate": "translate", "tr": "translate", "translation": "translate",
    "summarize": "summarize", "summary": "summarize", "sum": "summarize", "tldr": "summarize",
    "explain": "explain", "eli5": "explain",
    "context": "context", "ctx": "context",
    # writing transforms (from the AI-Neuromancer prompt library)
    "improve": "improve", "polish": "improve",
    "fix": "fix", "proofread": "fix", "spelling": "fix", "grammar": "fix",
    "shorten": "shorten", "shorter": "shorten", "concise": "shorten",
    "expand": "expand", "longer": "expand", "elaborate": "expand",
    "professional": "professional", "formal": "professional",
    "casual": "casual", "friendly": "casual",
    "persuasive": "persuasive",
    "rewrite": "rewrite", "rephrase": "rewrite",
    "outline": "outline",
    "keypoints": "keypoints", "bullets": "keypoints",
    "actions": "actions", "todos": "actions", "tasks": "actions",
    "debug": "debug",
    # ── multilingual aliases (FR · RU · ES · DE · PT) → the same language-preserving
    # ops above, so a non-English speaker triggers them in their own words. Single-word
    # only (the parser rejects multi-word replies); none collide with analyse/analyze
    # (reserved for tiktok). Recovered from the retired telegram-bot-navig nlp_aliases pack.
    "traduis": "translate", "traduire": "translate", "traduction": "translate",
    "переведи": "translate", "перевод": "translate", "перевести": "translate",
    "traduce": "translate", "traducir": "translate",
    "résume": "summarize", "résumer": "summarize", "résumé": "summarize",
    "резюме": "summarize", "кратко": "summarize", "суммируй": "summarize",
    "resumen": "summarize", "resumir": "summarize",
    "explique": "explain", "expliquer": "explain",
    "объясни": "explain", "объяснить": "explain",
    "explica": "explain", "explicar": "explain",
    "contexte": "context", "контекст": "context", "contexto": "context",
    "améliore": "improve", "ameliore": "improve", "améliorer": "improve",
    "улучши": "improve", "mejora": "improve", "mejorar": "improve",
    "corrige": "fix", "corriger": "fix", "corregir": "fix",
    "исправь": "fix", "исправить": "fix",
    "raccourcis": "shorten", "сократи": "shorten", "acorta": "shorten",
    "réécris": "rewrite", "reecris": "rewrite", "перепиши": "rewrite", "reescribe": "rewrite",
    "übersetze": "translate", "übersetzen": "translate", "traduza": "translate", "traduzir": "translate",
    "zusammenfassen": "summarize", "zusammenfassung": "summarize", "resuma": "summarize", "resumo": "summarize",
    "erkläre": "explain", "erklären": "explain",
    "verbessere": "improve", "melhore": "improve", "melhorar": "improve",
    "korrigiere": "fix", "corrija": "fix", "corrigir": "fix",
    "kürzen": "shorten", "encurtar": "shorten",
    "umschreiben": "rewrite", "reescreva": "rewrite", "reescrever": "rewrite",
    # ── owner-local / channel actions ──
    "save": "save", "bookmark": "save", "keep": "save",
    "refine": "refine", "redo": "refine", "again": "refine",
    "pin": "pin", "unpin": "unpin",
    "tiktok": "tiktok", "analyze": "tiktok", "analyse": "tiktok",
    # music-service link → the same track on every platform (song.link). Reply
    # "music"/"song" to a message with a Spotify/Apple/Deezer/… link. Works in
    # groups (owner-triggered), unlike the DM-only passive auto-reply.
    "music": "music", "song": "music",
}

# Sandboxed no-tools LLM ops — derived from ai_actions so the two never drift.
LLM_ACTIONS: frozenset[str] = frozenset(ai_actions.LLM_TOOLS)

# Actions permitted in business chats — every sandboxed text op (DATA-only,
# owner-private) plus local save. refine/pin/unpin/tiktok are bot-chat only.
BUSINESS_ACTIONS: frozenset[str] = LLM_ACTIONS | {"save"}

# Display label per action (emoji + name); falls back to Title-case for the rest.
_LLM_LABELS: dict[str, str] = {
    "translate": "🌍 Translation", "summarize": "📋 Summary",
    "explain": "💡 Explanation", "context": "🤔 Context",
    "improve": "✨ Improved", "fix": "✅ Corrected", "shorten": "✂️ Shortened",
    "expand": "📖 Expanded", "professional": "👔 Professional", "casual": "😎 Casual",
    "persuasive": "🎯 Persuasive", "rewrite": "🔁 Rewritten", "outline": "🗂 Outline",
    "keypoints": "📌 Key points", "actions": "☑️ Action items", "debug": "🐞 Debug",
}

# Bounded cache of the bot's OWN AI-output text, keyed by the sent message_id.
# Telegram's rich/AI replies come back with EMPTY reply text when a user replies
# to them, so this lets you CHAIN actions (e.g. translate → reply 'summarize' on
# the translation). LRU-trimmed; in-process only.
_RECENT_OUTPUT: "OrderedDict[int, str]" = OrderedDict()
_RECENT_MAX = 300


def remember_output(message_id: int | None, text: str) -> None:
    """Record one of the bot's AI outputs so a reply onto it can be re-read."""
    if not message_id or not text:
        return
    _RECENT_OUTPUT[int(message_id)] = text
    while len(_RECENT_OUTPUT) > _RECENT_MAX:
        _RECENT_OUTPUT.popitem(last=False)


# Actions that accept a trailing argument, e.g. "translate fr" → target language.
ARG_ACTIONS: frozenset[str] = frozenset({"translate"})


def parse(text: str) -> tuple[str | None, str]:
    """Resolve a reply's text → ``(action, arg)``.

    A bare keyword ("summarize", "/tldr") → ``(action, "")``. For arg-accepting
    actions, "translate fr" → ``("translate", "fr")``. Any other multi-word reply
    ("summarize this for the board") → ``(None, "")`` so normal replies aren't hijacked.
    """
    w = (text or "").strip().lower().lstrip("/").strip(" \t.!?:,")
    if not w:
        return None, ""
    if w in KEYWORDS:
        return KEYWORDS[w], ""
    head, _, rest = w.partition(" ")
    action = KEYWORDS.get(head)
    if action in ARG_ACTIONS and rest.strip():
        return action, rest.strip()
    return None, ""


def resolve(text: str) -> str | None:
    """Back-compat: the action only (see :func:`parse` for the argument)."""
    return parse(text)[0]


def help_text() -> str:
    """Human help card for the reply-keyword actions, rendered by ``/help transforms``.

    Sourced from ``_LLM_LABELS`` (the AI ops) so it never drifts from what actually
    dispatches; the media/owner keywords and language note are curated.
    """
    def _kw(action: str) -> str:
        label = _LLM_LABELS.get(action, action.title())
        emoji = label.split(" ", 1)[0] if " " in label else "•"
        return f"{emoji} <code>{action}</code>"

    ai = " · ".join(_kw(a) for a in _LLM_LABELS)
    return (
        "🎛 <b>Reply-keyword actions</b>\n"
        "Reply to any message with one of these words and I'll run it on that message:\n\n"
        f"<b>AI text</b>\n{ai}\n\n"
        "<b>Media</b>\n"
        "🎵 <code>music</code> / <code>song</code> — a music link → the same track everywhere\n"
        "🎬 <code>tiktok</code> / <code>analyse</code> — a TikTok link → an AI briefing\n\n"
        "<b>Owner</b>\n"
        "🔖 <code>save</code> · 🔁 <code>refine</code> · 📌 <code>pin</code> / <code>unpin</code>\n\n"
        "🌍 Also in FR · RU · ES · DE · PT — e.g. <code>traduis</code>, <code>переведи</code>, "
        "<code>resumen</code>, <code>übersetze</code>, <code>traduza</code>.\n"
        "💡 For translate, add a target: reply <code>translate fr</code>."
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _target_text(reply_to_msg: dict, chat_id: int | None, reply_id: int | None) -> str:
    """The text the action operates on: the replied-to message's text/caption,
    falling back to (1) the bot's own recent AI output cache — so chaining onto a
    rich reply works — then (2) the message catalog."""
    t = (reply_to_msg.get("text") or reply_to_msg.get("caption") or "").strip()
    if t:
        return t
    if reply_id and int(reply_id) in _RECENT_OUTPUT:
        return _RECENT_OUTPUT[int(reply_id)].strip()
    if chat_id and reply_id:
        try:
            from navig.store.telegram_catalog import TelegramCatalogStore

            row = TelegramCatalogStore().get_message_by_ref(int(chat_id), int(reply_id))
            return ((row or {}).get("text") or "").strip()
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _save_to_wiki(chat_id: int | None, text: str) -> bool:
    """Append the target text to the local wiki inbox. Returns success."""
    try:
        from navig.core.yaml_io import atomic_write_text
        from navig.platform.paths import config_dir

        inbox = config_dir() / "wiki" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        ts = int(time.time())
        atomic_write_text(
            inbox / f"reply_save_{ts}.md",
            f"---\nsource: telegram_reply\ntimestamp: {ts}\nchat_id: {chat_id}\n---\n\n{text}\n",
        )
        return True
    except Exception:  # noqa: BLE001
        logger.debug("reply save-to-wiki failed", exc_info=True)
        return False


async def _run_llm(action: str, target: str, *, is_owner: bool, arg: str = "") -> dict[str, Any]:
    return await ai_actions.run_text_action(action, target, is_owner=is_owner, arg=arg)


async def _no_text(channel: Any, chat_id: int, action: str) -> bool:
    """A resolved keyword reply OWNS the message even when the target has no
    readable text — never leak the bare keyword to the chat agent. Returns True."""
    await channel.send_message(
        chat_id,
        f"⚠️ I couldn't read any text in that message to {action}. "
        "Reply to a text message and try again.",
        parse_mode=None,
    )
    return True


# ── bot-chat dispatch (DM / group) ───────────────────────────────────────────


async def run_bot_reply(
    channel: Any,
    *,
    action: str,
    chat_id: int,
    user_id: int,
    reply_to_msg: dict,
    reply_to_message_id: int,
    is_group: bool,
    arg: str = "",
) -> bool:
    """Run a reply-keyword action in a normal bot chat. Replies in-chat.

    Returns True if the action handled the message (caller stops normal dispatch).
    A False return falls through to normal message handling.
    """
    is_owner = user_id in getattr(channel, "allowed_users", set())

    # Non-LLM actions are owner-only (LLM ops self-gate via run_text_action's policy).
    if action not in LLM_ACTIONS and not is_owner:
        return False

    if action in LLM_ACTIONS:
        target = _target_text(reply_to_msg, chat_id, reply_to_message_id)
        if not target:
            return await _no_text(channel, chat_id, action)
        res = await _run_llm(action, target, is_owner=is_owner, arg=arg)
        if not res.get("ok"):
            # 'not_permitted' → genuinely disallowed: fall through (normal message).
            # Any other failure (LLM error / empty) → OWN the message with a clear
            # error so the bare keyword never leaks to the chat agent.
            if res.get("reason") == "not_permitted":
                return False
            logger.warning("reply action %s failed: %s", action, res.get("reason"))
            await channel.send_message(
                chat_id, f"⚠️ Couldn't {action} that right now — try again in a moment.",
                parse_mode=None,
            )
            return True
        result = (res.get("result") or "").strip()
        if not result:
            await channel.send_message(
                chat_id, f"⚠️ Got an empty {action} result.", parse_mode=None
            )
            return True
        body = f"**{_LLM_LABELS.get(action, action.title())}**\n\n{result}"
        sent: Any = None
        try:
            sent = await channel.send_rich_message(
                chat_id, markdown=body, reply_to_message_id=reply_to_message_id
            )
        except Exception:  # noqa: BLE001
            sent = await channel.send_message(chat_id, result, parse_mode=None)
        # Remember our output so the user can chain another keyword onto it (the
        # rich reply itself comes back with empty text when replied to).
        try:
            remember_output(sent.get("message_id") if isinstance(sent, dict) else None, result)
        except Exception:  # noqa: BLE001
            pass
        return True

    if action == "save":
        target = _target_text(reply_to_msg, chat_id, reply_to_message_id)
        if not target:
            return await _no_text(channel, chat_id, action)
        ok = _save_to_wiki(chat_id, target)
        await channel.send_message(
            chat_id,
            "🔖 Saved to your wiki inbox." if ok else "⚠️ Couldn't save that.",
            parse_mode=None,
        )
        return True

    if action in ("pin", "unpin"):
        method = "pinChatMessage" if action == "pin" else "unpinChatMessage"
        try:
            res = await channel._api_call(
                method,
                {"chat_id": chat_id, "message_id": reply_to_message_id,
                 "disable_notification": True},
            )
        except Exception:  # noqa: BLE001
            res = None
        if res is not None:
            await channel.send_message(
                chat_id, "📌 Pinned." if action == "pin" else "📌 Unpinned.", parse_mode=None
            )
        else:
            await channel.send_message(
                chat_id, "📌 Couldn't pin — I may need admin rights here.", parse_mode=None
            )
        return True

    if action == "refine":
        return await _refine(channel, chat_id, user_id, reply_to_msg, reply_to_message_id)

    if action == "tiktok":
        target = _target_text(reply_to_msg, chat_id, reply_to_message_id)
        try:
            from navig.telegram import permissions
            from navig.telegram import tiktok_actions as tt

            url = tt.engine.extract_url(target)
            if not url:
                await channel.send_message(
                    chat_id, "🎵 No TikTok link found in that message.", parse_mode=None
                )
                return True
            if not permissions.can_use("download", is_owner=is_owner):
                await channel.send_message(chat_id, "⛔ Not permitted.", parse_mode=None)
                return True
            await tt._do_analyse(channel, chat_id, url)
            return True
        except Exception:  # noqa: BLE001
            logger.debug("reply tiktok action failed", exc_info=True)
            await channel.send_message(
                chat_id, "🎵 Couldn't analyse that link right now.", parse_mode=None
            )
            return True

    if action == "music":
        target = _target_text(reply_to_msg, chat_id, reply_to_message_id)
        try:
            from navig.telegram import music_actions as music

            url = music.find_music_url(target)
            if not url:
                await channel.send_message(
                    chat_id, "🎵 No music link found in that message.", parse_mode=None
                )
                return True
            if not await music.resolve_reply(channel, chat_id, reply_to_message_id, url):
                await channel.send_message(
                    chat_id, "🎵 Couldn't find that track on song.link.", parse_mode=None
                )
            return True
        except Exception:  # noqa: BLE001
            logger.debug("reply music action failed", exc_info=True)
            await channel.send_message(
                chat_id, "🎵 Couldn't convert that link right now.", parse_mode=None
            )
            return True

    return False


async def _refine(
    channel: Any, chat_id: int, user_id: int, reply_to_msg: dict, reply_to_message_id: int
) -> bool:
    """Re-run the agent to produce a deeper / better answer for the replied-to text."""
    target = _target_text(reply_to_msg, chat_id, reply_to_message_id)
    on_message = getattr(channel, "on_message", None)
    if not target or not on_message:
        return False
    prompt = (
        "Improve and expand on the following — add depth, accuracy, and useful "
        f"detail:\n\n{target}"
    )
    typing = asyncio.create_task(channel._keep_typing(chat_id))
    try:
        response = await on_message(
            channel="telegram", user_id=str(user_id), message=prompt,
            metadata={"tier_override": "big", "refine_signal": True},
        )
    finally:
        typing.cancel()
        try:
            await typing
        except asyncio.CancelledError:
            pass
    if not response:
        return False
    try:
        await channel._send_response(chat_id, response, prompt, user_id=user_id)
    except Exception:  # noqa: BLE001
        await channel.send_message(chat_id, response, parse_mode=None)
    return True


# ── business-chat dispatch (owner-only, private) ─────────────────────────────


async def run_business_reply(
    channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None
) -> bool:
    """Owner replied to a business message with a keyword → run a sandboxed action
    and DM the result PRIVATELY to the owner (the counterparty never sees it).

    Best-effort deletes the owner's keyword message from the business chat.
    Owner-only; restricted to the ``BUSINESS_ACTIONS`` subset. Returns True if an
    action ran (caller stops further handling).
    """
    if not (is_owner and owner_id):
        return False
    reply = msg.get("reply_to_message")
    if not isinstance(reply, dict):
        return False
    action, arg = parse(msg.get("text") or "")
    if not action or action not in BUSINESS_ACTIONS:
        return False

    chat_id = (msg.get("chat") or {}).get("id")
    rid = reply.get("message_id")
    target = _target_text(reply, chat_id, rid)
    if not target:
        # Resolved keyword but no readable text → tell the owner privately rather
        # than silently doing nothing.
        try:
            await channel.send_message(
                owner_id, f"⚠️ I couldn't read any text in that message to {action}.",
                parse_mode=None,
            )
        except Exception:  # noqa: BLE001
            pass
        return True

    if action in LLM_ACTIONS:
        res = await _run_llm(action, target, is_owner=True, arg=arg)
        if not res.get("ok"):
            # Tell the owner privately rather than silently doing nothing.
            logger.warning("business reply action %s failed: %s", action, res.get("reason"))
            try:
                await channel.send_message(
                    owner_id, f"⚠️ Couldn't {action} that message right now.", parse_mode=None
                )
            except Exception:  # noqa: BLE001
                pass
            return True
        body = f"**{_LLM_LABELS.get(action, action.title())}**\n\n{res['result']}"
    elif action == "save":
        if not _save_to_wiki(chat_id, target):
            return False
        body = "🔖 Saved to your wiki inbox."
    else:  # not reachable given BUSINESS_ACTIONS, but keep total
        return False

    try:
        # Rich markdown (bold label, code blocks, expandable quotes) with a plain
        # DM fallback — mirrors run_bot_reply. send_rich_message already degrades to
        # HTML where rich isn't supported; the plain send covers a hard failure.
        try:
            await channel.send_rich_message(owner_id, markdown=body)
        except Exception:  # noqa: BLE001
            await channel.send_message(owner_id, body, parse_mode=None)
    except Exception:  # noqa: BLE001
        logger.debug("business reply-action DM failed", exc_info=True)
        return False

    # Best-effort: remove the owner's keyword message from the business chat so the
    # counterparty never sees the trigger word (needs can_delete on the connection).
    # Business chats require deleteBusinessMessages, not deleteMessage.
    bcid = msg.get("business_connection_id")
    mid = msg.get("message_id")
    try:
        if bcid and mid is not None:
            await channel._api_call(
                "deleteBusinessMessages",
                {"business_connection_id": bcid, "message_ids": [mid]},
            )
    except Exception:  # noqa: BLE001
        pass
    return True
