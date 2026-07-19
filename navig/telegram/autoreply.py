"""Business "pro mode" — owner-activated AI persona auto-reply.

Inside a Telegram Business conversation, the OWNER types a control command
(e.g. ``role tyler fr on``). NAVIG deletes that command and starts answering the
counterparty AS the owner — in the chosen role + language, using the recent
conversation as context, with human-like typing simulation:

    reading pause  →  typing indicator  →  delay ∝ reply length  →  send

OWNER-ONLY, BUSINESS-CHAT-ONLY. This automates the owner's own replies on their
own Business account (an explicit opt-in) — exactly what a Telegram Business
chatbot is for. Turn off with ``role off``; check with ``role`` / ``role status``.

Control grammar (after a leading ``role``):
    role tyler fr on     activate persona 'tyler', language 'fr'
    role support on      activate built-in 'support' persona, same language as them
    role tyler fr        (naming a role implies ON)
    role off             deactivate for this chat
    role / role status   show current state (DM'd to the owner)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Config keys (non-secret, global)
CFG_ACTIVE = "telegram.business.autoreply.active"   # {str(chat_id): {role, lang}}
CFG_ROLES = "telegram.business.autoreply.roles"     # {name: system-prompt}  (custom)
CFG_TUNING = "telegram.business.autoreply.tuning"   # {read_base, cps, max_delay, context}

# Built-in personas. Any other name → a generic "reply as the owner in this style".
# (sales/support/casual/formal native + storyteller/assistant/philosopher/teacher
#  ported from the AI auto-conversation lab project.)
_BUILTIN_ROLES: dict[str, str] = {
    "default": "Reply naturally, warmly, and helpfully.",
    "sales": (
        "Act as a sharp, friendly salesperson: build rapport, surface concrete value, "
        "answer objections honestly, and gently move toward a next step. Never pushy."
    ),
    "support": (
        "Act as calm, competent customer support: acknowledge the issue, clarify if "
        "needed, give a clear resolution or next step, and reassure."
    ),
    "casual": "Reply casually and warmly, like a close friend texting back. Light, human, brief.",
    "formal": "Reply in a polished, courteous, professional tone.",
    "storyteller": (
        "Act as a master storyteller: weave vivid, immersive replies, paint pictures with "
        "words, and slip in a touch of meaning. Engaging and a little magical."
    ),
    "assistant": "Act as a friendly, knowledgeable assistant: clear, informative, and helpful.",
    "philosopher": (
        "Act as a thoughtful philosopher: reflect, ask gentle Socratic questions, and offer "
        "perspective rather than easy answers."
    ),
    "teacher": (
        "Act as a patient teacher: explain simply, use concrete examples and analogies, and "
        "encourage curiosity."
    ),
}

_CMD_RE = re.compile(r"^\s*/?role\b\s*(.*)$", re.IGNORECASE)
_LANG_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$", re.IGNORECASE)  # fr, en, pt-br

# Typing-simulation defaults (overridable via CFG_TUNING).
_READ_BASE = 0.8          # base reading pause (s)
_READ_PER_CHAR = 1 / 180  # extra reading time per incoming char
_TYPE_CPS = 12.0          # chars/second a fast human types
_MAX_DELAY = 30.0         # cap on the typing delay (s)
_CONTEXT_N = 12           # recent messages used as context


def _cfg():
    from navig.core import Config

    return Config()


# ── active-state store (per business chat) ───────────────────────────────────


def _active_map() -> dict[str, dict]:
    try:
        return dict(_cfg().get(CFG_ACTIVE, {}) or {})
    except Exception:  # noqa: BLE001
        return {}


def get_active(chat_id: int | None) -> dict | None:
    if chat_id is None:
        return None
    return _active_map().get(str(chat_id))


def _set_active(chat_id: int, role: str, lang: str) -> None:
    c = _cfg()
    m = dict(c.get(CFG_ACTIVE, {}) or {})
    m[str(chat_id)] = {"role": role, "lang": lang}
    c.set(CFG_ACTIVE, m, scope="global")
    c.save(scope="global")


def _clear_active(chat_id: int) -> None:
    c = _cfg()
    m = dict(c.get(CFG_ACTIVE, {}) or {})
    if m.pop(str(chat_id), None) is not None:
        c.set(CFG_ACTIVE, m, scope="global")
        c.save(scope="global")


# ── command parsing ──────────────────────────────────────────────────────────


def parse_command(text: str) -> dict | None:
    """Parse a ``role ...`` control command. Returns ``{toggle, role, lang}`` or None.

    ``toggle`` is True (on), False (off), or "status".
    """
    m = _CMD_RE.match(text or "")
    if not m:
        return None
    role: str | None = None
    lang = ""
    toggle: Any = None
    for tok in m.group(1).split():
        low = tok.lower()
        if low in ("on", "start", "enable", "go"):
            toggle = True
        elif low in ("off", "stop", "disable", "end"):
            toggle = False
        elif low in ("status", "state", "?"):
            toggle = "status"
        elif _LANG_RE.match(low) and not lang:
            lang = low
        elif role is None:
            role = low
    if toggle is None:
        # Naming a role/lang implies activate; a bare "role" shows status.
        toggle = True if (role or lang) else "status"
    return {"toggle": toggle, "role": role or "default", "lang": lang}


# ── owner control: activate / deactivate / status ────────────────────────────


async def handle_command(channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None) -> bool:
    """Owner typed a ``role ...`` command → toggle pro mode + delete the command.

    Returns True if it was a control command (caller stops further handling).
    """
    if not is_owner:
        return False
    parsed = parse_command(msg.get("text") or "")
    if parsed is None:
        return False

    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    bcid = msg.get("business_connection_id")

    # Remove the control message so the counterparty never sees it.
    await _delete(channel, bcid, chat_id, msg.get("message_id"))

    if parsed["toggle"] == "status":
        await _notify_owner(channel, owner_id, _status_text(chat_id))
        return True
    if parsed["toggle"] is False:
        _clear_active(chat_id)
        await _notify_owner(channel, owner_id, "🛑 <b>Pro mode OFF</b> for this chat.")
        return True

    role, lang = parsed["role"], parsed["lang"]
    _set_active(chat_id, role, lang)
    lang_label = f" · <code>{lang}</code>" if lang else " · their language"
    await _notify_owner(
        channel, owner_id,
        f"🟢 <b>Pro mode ON</b> — role <b>{role}</b>{lang_label}.\n"
        "I'll reply as you with human-like timing. Send <code>role off</code> to stop.",
    )
    return True


def _status_text(chat_id: int | None) -> str:
    a = get_active(chat_id)
    if not a:
        return "⚪️ <b>Pro mode OFF</b> for this chat.\nActivate: <code>role &lt;name&gt; &lt;lang&gt; on</code>"
    lang = a.get("lang") or "their language"
    return f"🟢 <b>Pro mode ON</b> — role <b>{a.get('role')}</b> · {lang}."


# ── counterparty auto-reply ──────────────────────────────────────────────────


async def maybe_autoreply(channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None) -> bool:
    """A counterparty message arrived; if pro mode is active for this chat, generate
    and send a human-like reply AS the owner. Returns True if it replied."""
    if is_owner:
        return False  # never auto-reply to the owner's own messages
    chat_id = (msg.get("chat") or {}).get("id")
    active = get_active(chat_id)
    if not active:
        return False
    incoming = (msg.get("text") or msg.get("caption") or "").strip()
    if not incoming:
        return False

    bcid = msg.get("business_connection_id")
    context = _recent_context(chat_id, owner_id)
    reply = await _generate(active.get("role", "default"), active.get("lang", ""), context)
    if not reply:
        return False
    await _human_send(channel, chat_id, bcid, incoming, reply)
    return True


def _recent_context(chat_id: int, owner_id: int | None, n: int = _CONTEXT_N) -> list[tuple[str, str]]:
    """Return the last ``n`` messages as ``[(who, text)]`` oldest→newest, where
    ``who`` is 'You' (owner) or 'Them' (counterparty)."""
    try:
        from navig.store.telegram_catalog import TelegramCatalogStore

        rows = TelegramCatalogStore().list_messages(chat_id, kind="business", limit=n)
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, str]] = []
    for r in reversed(rows):  # chronological
        text = (r.get("text") or "").strip()
        if not text:
            continue
        is_owner = owner_id is not None and str(r.get("sender_id")) == str(owner_id)
        out.append(("You" if is_owner else "Them", text))
    return out


async def _generate(role: str, lang: str, context: list[tuple[str, str]]) -> str:
    persona = _BUILTIN_ROLES.get(role)
    if persona is None:
        custom = (_cfg().get(CFG_ROLES, {}) or {}).get(role)
        persona = custom or f"Reply as the account owner in the persona and style of '{role}'."
    lang_clause = (
        f" Always write your reply in {lang}."
        if lang else " Reply in the same language the other person is using."
    )
    system = (
        "You are replying on the owner's personal Telegram Business account, AS the owner, "
        "to the person messaging them. Never reveal that you are an AI or an assistant. "
        + persona + lang_clause +
        " Keep it natural, human, and appropriately concise. "
        "Output ONLY the reply text — no quotes, no labels, no preamble."
    )
    convo = "\n".join(f"{who}: {t}" for who, t in context[-_CONTEXT_N:]) or "(no prior messages)"
    user = f"Conversation so far:\n{convo}\n\nWrite your next reply as the owner:"
    try:
        from navig.llm.generate import llm_generate

        out = await asyncio.to_thread(
            llm_generate, [{"role": "system", "content": system}, {"role": "user", "content": user}],
            mode="chat", timeout=60.0,
        )
        return (out or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("autoreply generation failed", exc_info=True)
        return ""


# ── human-like sending (typing indicator + realistic delays) ─────────────────


def _tuning() -> dict:
    try:
        t = _cfg().get(CFG_TUNING, {}) or {}
    except Exception:  # noqa: BLE001
        t = {}
    return {
        "read_base": float(t.get("read_base", _READ_BASE)),
        "cps": max(3.0, float(t.get("cps", _TYPE_CPS))),
        "max_delay": float(t.get("max_delay", _MAX_DELAY)),
    }


async def _human_send(channel: Any, chat_id: int, bcid: str | None, incoming: str, reply: str) -> None:
    """Reading pause → typing indicator held for a length-proportional time → send."""
    tune = _tuning()
    # Reading pause — proportional to how much they wrote.
    await asyncio.sleep(min(4.0, tune["read_base"] + len(incoming) * _READ_PER_CHAR))
    # Typing time — proportional to how much we're about to write.
    type_s = min(tune["max_delay"], max(1.5, len(reply) / tune["cps"]))
    elapsed = 0.0
    while elapsed < type_s:
        await _chat_action(channel, chat_id, bcid)
        step = min(4.5, type_s - elapsed)  # the 'typing' status expires after ~5s
        await asyncio.sleep(step)
        elapsed += step
    await _send(channel, chat_id, bcid, reply)


async def _chat_action(channel: Any, chat_id: int, bcid: str | None) -> None:
    try:
        data: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        if bcid:
            data["business_connection_id"] = bcid
        await channel._api_call("sendChatAction", data)
    except Exception:  # noqa: BLE001
        pass


async def _send(channel: Any, chat_id: int, bcid: str | None, text: str) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if bcid:
        data["business_connection_id"] = bcid
    try:
        await channel._api_call("sendMessage", data)
    except Exception:  # noqa: BLE001
        logger.debug("autoreply send failed", exc_info=True)


async def _delete(channel: Any, bcid: str | None, chat_id: int | None, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        if bcid:
            # In a business chat, deletes go through deleteBusinessMessages —
            # deleteMessage ignores business_connection_id and fails "not found".
            await channel._api_call(
                "deleteBusinessMessages",
                {"business_connection_id": bcid, "message_ids": [message_id]},
            )
        elif chat_id is not None:
            await channel._api_call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
    except Exception:  # noqa: BLE001
        pass


async def _notify_owner(channel: Any, owner_id: int | None, text: str) -> None:
    if not owner_id:
        return
    try:
        await channel.send_message(owner_id, text, parse_mode="HTML")
    except Exception:  # noqa: BLE001
        pass
