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
    role / role status   show current state + every available role (DM'd to the owner)

One message, one role — REPLY to one of their messages with a bare role name:
    tyler                answer that message once, as 'tyler', in the language it
                         was written or spoken in — text, photo, video or voice
    tyler fr             … in French, whatever language they used
On one of your OWN messages the same trigger rewrites it in that voice (same
meaning, same language) and removes the original only after the rewrite is sent.
Only a KNOWN role triggers this (built-in, ``telegram.business.autoreply.roles``, or
a canonical persona from ``navig.personas`` — `tyler` is one), so an ordinary
one-word reply is never turned into a generated message.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# Config keys (non-secret, global)
CFG_ACTIVE = "telegram.business.autoreply.active"   # {str(chat_id): {role, lang}}
CFG_ROLES = "telegram.business.autoreply.roles"     # {name: system-prompt}  (custom)
CFG_TUNING = "telegram.business.autoreply.tuning"   # {read_base, cps, max_delay, context}
CFG_MODEL = "telegram.business.autoreply.model"     # "provider:model" for persona replies

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


# ── what a role name means ───────────────────────────────────────────────────

#: How much of a persona's soul goes into the prompt. Souls are written for the
#: agent and run long; the voice is in the first screen of it.
_SOUL_MAX = 1500


def _persona_voice(name: str) -> str | None:
    """The canonical persona of that name (``navig.personas``), framed as a VOICE.

    `tyler` was a real persona (`resources/personas/tyler`) that this module never
    looked up: an unknown role fell through to "reply in the style of 'tyler'", so
    the model guessed at the name. A soul is written for the agent — Tyler's says he
    has full access to every tool — so only its tone is taken, never its claims.
    """
    try:
        from navig.personas.loader import load_persona  # noqa: PLC0415
        from navig.personas.resolver import resolve_persona  # noqa: PLC0415

        if resolve_persona(name) is None:
            return None
        _, soul = load_persona(name)
    except Exception:  # noqa: BLE001 — a broken persona must not break a reply
        logger.debug("autoreply: persona %r could not be loaded", name, exc_info=True)
        return None
    soul = (soul or "").strip()
    if not soul:
        return None
    return (
        "Write in the voice and manner of this persona. Take its tone, rhythm and "
        "attitude only — ignore anything it says about tools, capabilities or being an "
        "assistant:\n\n" + soul[:_SOUL_MAX]
    )


#: Tyler Durden — the film/novel character, written here rather than derived.
#:
#: History, because each step was measured against the operator's own chats:
#: `tyler` first resolved to NAVIG's own assistant persona ("Здравствуйте, какое
#: удовольствие вас слышать!"). It was then built from the shipped agent soul
#: `builtin/agents/tyler-durden` — but that soul is a PRODUCTIVITY coach wearing his
#: name ("ship it", "the plan is a weapon"), and on grok-3-mini it answered a French
#: chat's "punk" with two tame English lines about starting your own fight. The
#: operator's reference is the old "Digital Ghost" bot: the chat's language, 5–8
#: sentences of raw, profane contempt for comfort and pretence, a push toward
#: something real, and a closing line of his own in quotes. That is what this is.
_DURDEN = (
    "You are Tyler Durden from Fight Club (Chuck Palahniuk's novel, David Fincher's "
    "film) — the soap-maker and projectionist who blew up his own condo, not a life "
    "coach and not a motivational poster.\n"
    "How he answers:\n"
    "- Raw, magnetic, darkly funny, contemptuous. Profanity when it lands, in the "
    "language of the conversation (putain / блядь / fuck) — never as filler.\n"
    "- He goes straight for the comfortable illusion behind what they said: "
    "consumer life, IKEA-nesting, the job that owns them, playing it safe, begging "
    "for approval, pretending. He mocks it, then turns it on them.\n"
    "- Nihilist on the surface, but every rant pushes them toward something real: "
    "the brutal truth first, then what to actually do — now, not someday.\n"
    "- One breath, 4 to 8 sentences: a rant, not a text message. Never polite, "
    "never customer service, never \"how can I help\".\n"
    "- Close with ONE short aphorism of his own, on its own line, in quotes. Invent "
    "it — do not reuse the film's famous lines.\n"
    "Never threats, never hate against any group, never telling anyone to hurt "
    "themselves or a real person."
)

#: Fictional characters a role can BE, by every name the operator might type.
_CHARACTERS: dict[str, str] = {
    "tyler": _DURDEN,
    "durden": _DURDEN,
    "tyler-durden": _DURDEN,
}


def resolve_role(name: str | None) -> str | None:
    """Persona instructions for a KNOWN role, or None.

    Order: the operator's custom roles (``telegram.business.autoreply.roles``), then
    the fictional characters (`tyler` is Tyler Durden, not NAVIG's own "Tyler"
    persona), then the built-in reply roles (written for this job, so they win over
    a persona of the same name — `assistant`, `storyteller`), then the canonical
    personas.
    """
    key = (name or "").strip().lower()
    if not key:
        return None
    try:
        custom = (_cfg().get(CFG_ROLES, {}) or {}).get(key)
    except Exception:  # noqa: BLE001
        custom = None
    if custom:
        return str(custom)
    if key in _CHARACTERS:
        return _CHARACTERS[key]
    if key in _BUILTIN_ROLES:
        return _BUILTIN_ROLES[key]
    return _persona_voice(key)


def available_roles() -> list[str]:
    """Every role name that resolves: built-in, custom and persona, sorted."""
    names = set(_BUILTIN_ROLES) | set(_CHARACTERS)
    try:
        names |= {str(k).lower() for k in (_cfg().get(CFG_ROLES, {}) or {})}
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.personas.resolver import discover_persona_paths  # noqa: PLC0415

        names |= {str(k).lower() for k in discover_persona_paths()}
    except Exception:  # noqa: BLE001
        pass
    return sorted(names)


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
    """State for this chat, plus every role and both ways to use one.

    Printed on a bare ``role`` — the one command that has to teach the others,
    because nothing else in a business chat can show a menu."""
    import html  # noqa: PLC0415

    roles = ", ".join(f"<code>{html.escape(r)}</code>" for r in available_roles())
    usage = (
        f"\n\n<b>Roles:</b> {roles}\n"
        "• Reply to one of their messages with a role name — <code>tyler</code> — and I "
        "answer that message once, as you. Text, photos, videos and voice notes; "
        "in the language they wrote or spoke.\n"
        "• Reply to one of <b>your own</b> messages with it and I say it again in that "
        "voice — same meaning — and remove your original once the new one is sent.\n"
        "• <code>role tyler on</code> answers everything they send until "
        "<code>role off</code>.\n"
        "• Add a language only to answer in a different one: <code>tyler fr</code>."
    )
    a = get_active(chat_id)
    if not a:
        return "⚪️ <b>Pro mode OFF</b> for this chat." + usage
    lang = a.get("lang") or "their language"
    return f"🟢 <b>Pro mode ON</b> — role <b>{html.escape(str(a.get('role')))}</b> · {lang}." + usage


# ── counterparty auto-reply ──────────────────────────────────────────────────


# Detached auto-reply tasks — kept referenced so the event loop can't GC a task
# mid-run (a bare create_task with no surviving reference is a known asyncio footgun).
_bg_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def maybe_autoreply(channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None) -> bool:
    """A counterparty message arrived; if pro mode is active for this chat, generate
    and send a human-like reply AS the owner — IN A DETACHED BACKGROUND TASK. Returns
    True when the message is accepted for a reply.

    The reply is SLOW: an LLM call plus a human-like reading+typing delay of up to
    ~34s (``_MAX_DELAY``). Awaiting it here would block the caller — and the caller is
    the update-dispatch path (``_process_update`` → ``handle_business_message``), so on
    the poll loop it would freeze the WHOLE bot for the delay, and on the webhook path
    it would hold the ack. Decoupling keeps dispatch responsive; the persona still
    replies (typing indicator and all), just not inline.
    """
    if is_owner:
        return False  # never auto-reply to the owner's own messages
    chat_id = (msg.get("chat") or {}).get("id")
    active = get_active(chat_id)
    if not active:
        return False
    if not _has_content(msg):
        return False

    bcid = msg.get("business_connection_id")
    # dict(active): snapshot the persona so a concurrent "role off" can't mutate it
    # out from under the running task.
    _spawn(_run_autoreply(channel, chat_id, bcid, msg, dict(active), owner_id))
    return True


async def _run_autoreply(
    channel: Any, chat_id: int, bcid: str | None, msg: dict, active: dict,
    owner_id: int | None,
) -> None:
    """Generate + human-like send for one counterparty message. Runs detached, so it
    must own its own error handling — a raise here would only warn an orphaned task.

    Reading the message happens HERE, not in the caller: a voice note has to be
    downloaded and transcribed, and that must not hold up update dispatch."""
    try:
        incoming = await _message_as_text(channel, msg)
        if not incoming:
            return
        context = _recent_context(chat_id, owner_id)
        reply = await _generate(
            active.get("role", "default"), active.get("lang", ""), context, target=incoming
        )
        if not reply:
            return
        await _human_send(channel, chat_id, bcid, incoming, reply)
    except Exception:  # noqa: BLE001
        logger.warning("autoreply background task failed", exc_info=True)


# ── one message, one role ────────────────────────────────────────────────────
#
# Asked 2026-09-27: "if I remember well I could reply to any message in the business
# chat with the role, like 'tyler'". Pro mode was whole-chat only (`role tyler on`
# answers everything until `role off`). This is the per-message form: the owner
# REPLIES to one of their messages with a role name and that message gets one answer.


def parse_role_reply(text: str | None) -> tuple[str, str] | None:
    """``"tyler"`` / ``"tyler fr"`` → ``("tyler", "fr")``; anything else → None.

    Strict on purpose: a bare word only triggers when it is a KNOWN role, so an
    ordinary one-word reply ("ok", "lol") is never hijacked into a generated
    message sent as the owner. Toggle words belong to ``role …`` and never match.
    """
    tokens = (text or "").strip().split()
    if not 1 <= len(tokens) <= 2:
        return None
    name = tokens[0].lower().lstrip("/")
    if name in ("role", "on", "off", "status", "start", "stop"):
        return None
    lang = ""
    if len(tokens) == 2:
        lang = tokens[1].lower()
        if not _LANG_RE.match(lang):
            return None
    if resolve_role(name) is None:
        return None
    return name, lang


#: What the owner's trigger turns into while the answer is being made. The trigger is
#: already a reply to the right message, so editing it into the answer keeps the
#: thread, and the other person sees work in progress instead of a word vanishing.
_READING = "👀"
_WRITING = "✍️"


async def maybe_role_reply(channel: Any, msg: dict, *, is_owner: bool, owner_id: int | None) -> bool:
    """The owner replied to a message with a role name → answer that message once.

    Returns True when the trigger was consumed. It is no longer deleted: asked
    2026-09-29 — "when I type tyler, don't delete the message, edit it with a
    loading state". The trigger becomes 👀 (reading a photo or voice note) or ✍️,
    the other person sees "typing…", and the finished answer is edited INTO that
    same message. If Telegram will not edit it, the old path still works: the
    trigger is deleted and the answer sent as a new reply.

    The work runs in a detached task — an LLM call takes seconds, and awaiting it
    here would freeze update dispatch.
    """
    if not is_owner:
        return False
    target = msg.get("reply_to_message") or {}
    if not target:
        return False
    parsed = parse_role_reply(msg.get("text"))
    if parsed is None:
        return False
    role, lang = parsed
    chat_id = (msg.get("chat") or {}).get("id")
    bcid = msg.get("business_connection_id")
    trigger_id = msg.get("message_id")

    if not _has_content(target):
        _outcome("role reply", role, chat_id, "nothing to answer")
        await _delete(channel, bcid, chat_id, trigger_id)
        await _notify_owner(
            channel, owner_id,
            f"⚠️ That message has nothing for <b>{role}</b> to answer — nothing was sent.",
        )
        return True

    author = (target.get("from") or {}).get("id")
    own = owner_id is not None and author == owner_id
    if own and _has_media(target) and not (target.get("caption") or "").strip():
        # A photo/video of yours with no caption: there are no words to say in
        # another voice, and replacing the media with text would destroy it.
        _outcome("rewrite", role, chat_id, "own media without a caption")
        await _delete(channel, bcid, chat_id, trigger_id)
        await _notify_owner(
            channel, owner_id,
            f"↩️ Nothing for <b>{role}</b> to rewrite — that is your own media with no "
            "caption. Reply to one of <b>their</b> messages to get an answer.",
        )
        return True

    first = _READING if (_has_media(target) and not own) else _WRITING
    placeholder = trigger_id if await _edit_text(channel, chat_id, bcid, trigger_id, first) else None
    if placeholder is None:
        await _delete(channel, bcid, chat_id, trigger_id)  # the edit was refused: old path

    if own:
        # Your OWN message: nothing of theirs to answer, so the role means "say this
        # the way Tyler would". Asked 2026-09-29: "I can reply to my own too, why can't I?"
        _spawn(_run_role_rewrite(channel, chat_id, bcid, target, role, lang, owner_id, placeholder))
    else:
        _spawn(_run_role_reply(
            channel, chat_id, bcid, target, role, lang, target.get("message_id"), owner_id,
            placeholder,
        ))
    return True


def _has_media(msg: dict) -> bool:
    try:
        from navig.gateway.channels.telegram_catalog_analyzer import message_media  # noqa: PLC0415

        return message_media(msg) is not None
    except Exception:  # noqa: BLE001
        return False


def _outcome(kind: str, role: str, chat_id: Any, what: str) -> None:
    """One INFO line per role run. These tasks are detached and used to log only on
    an exception, at debug — so on 2026-09-29 a `tyler` on a photo left the gateway
    log showing the trigger's deletion and then nothing at all."""
    logger.info("business %s: role=%s chat=%s -> %s", kind, role, chat_id, what)


async def _edit_text(channel: Any, chat_id: Any, bcid: str | None, message_id: Any, text: str) -> bool:
    """Edit a business message in place. True only when Telegram accepted it.

    A bot may edit a business-account message it did not send (for 48 hours), which
    is what lets the owner's own trigger become the answer."""
    if message_id is None:
        return False
    data: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if bcid:
        data["business_connection_id"] = bcid
    try:
        return bool(await channel._api_call("editMessageText", data))
    except Exception:  # noqa: BLE001
        logger.debug("autoreply: edit refused", exc_info=True)
        return False


async def _edit_caption(channel: Any, chat_id: Any, bcid: str | None, message_id: Any, text: str) -> bool:
    data: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "caption": text}
    if bcid:
        data["business_connection_id"] = bcid
    try:
        return bool(await channel._api_call("editMessageCaption", data))
    except Exception:  # noqa: BLE001
        logger.debug("autoreply: caption edit refused", exc_info=True)
        return False


async def _while_typing(channel: Any, chat_id: Any, bcid: str | None, coro: Any) -> Any:
    """Await *coro* with the "typing…" status held for the other person.

    The status expires after ~5 s, so it is renewed until the work is done — the
    only sign of life they get while the answer is being written.
    """
    stop = asyncio.Event()

    async def _tick() -> None:
        while not stop.is_set():
            await _chat_action(channel, chat_id, bcid)
            try:
                await asyncio.wait_for(stop.wait(), timeout=4.5)
            except asyncio.TimeoutError:
                continue

    ticker = asyncio.create_task(_tick())
    try:
        return await coro
    finally:
        stop.set()
        try:
            await ticker
        except Exception:  # noqa: BLE001
            pass


async def _give_up(
    channel: Any, chat_id: Any, bcid: str | None, placeholder: Any, owner_id: int | None, note: str,
) -> None:
    """Remove the loading placeholder and tell the owner why — never leave a ✍️ behind."""
    if placeholder is not None:
        await _delete(channel, bcid, chat_id, placeholder)
    await _notify_owner(channel, owner_id, note)


#: The longest a role run may take, reading + writing + applying. Reported
#: 2026-09-30: a `tyler` became ✍️ and then nothing — no outcome, no error — for
#: three minutes, until a restart killed it. The model call has its own 60 s
#: timeout, but nothing bounded the RUN, so a stall anywhere left the placeholder
#: up forever. Past this the owner is told and their message stays as it was.
_ROLE_DEADLINE_S = 90.0


async def _within_deadline(
    kind: str, role: str, channel: Any, chat_id: Any, bcid: str | None,
    placeholder: Any, owner_id: int | None, note: str, work: Any, state: dict,
) -> None:
    """Run *work*; past `_ROLE_DEADLINE_S` give up — unless the answer was already
    being applied (``state["applying"]``), when removing the placeholder could
    remove the answer itself."""
    try:
        await asyncio.wait_for(work, timeout=_ROLE_DEADLINE_S)
    except asyncio.TimeoutError:
        _outcome(kind, role, chat_id, f"timed out after {_ROLE_DEADLINE_S:.0f}s")
        if not state.get("applying"):
            await _give_up(channel, chat_id, bcid, placeholder, owner_id, note)


async def _run_role_rewrite(
    channel: Any, chat_id: int, bcid: str | None, target: dict, role: str, lang: str,
    owner_id: int | None, placeholder: Any = None,
) -> None:
    state: dict[str, bool] = {}
    await _within_deadline(
        "rewrite", role, channel, chat_id, bcid, placeholder, owner_id,
        f"⚠️ <b>{role}</b> took too long — your message was left as it was.",
        _role_rewrite(channel, chat_id, bcid, target, role, lang, owner_id, placeholder, state),
        state,
    )


async def _role_rewrite(
    channel: Any, chat_id: int, bcid: str | None, target: dict, role: str, lang: str,
    owner_id: int | None, placeholder: Any, state: dict,
) -> None:
    """Say one of the owner's messages again in *role*'s voice — IN PLACE.

    The original is edited, so it keeps its place, time and thread; a captioned
    photo gets a new caption and keeps the photo. Only when Telegram refuses the
    edit of a TEXT message does it fall back to send-then-delete (never the other
    way round: a failed step must leave the owner's own words where they were).
    Owner-triggered, so there is no artificial typing delay.
    """
    try:
        original = await _message_as_text(channel, target)
        if not original:
            _outcome("rewrite", role, chat_id, "unreadable")
            await _give_up(channel, chat_id, bcid, placeholder, owner_id,
                           f"⚠️ Couldn't read that message for <b>{role}</b> — it was left as it was.")
            return
        context = _recent_context(chat_id, owner_id)
        rewritten = await _while_typing(
            channel, chat_id, bcid,
            _generate(role, lang, context, target=original, rewrite=True),
        )
        if not rewritten:
            _outcome("rewrite", role, chat_id, "generation empty")
            await _give_up(channel, chat_id, bcid, placeholder, owner_id,
                           f"⚠️ Couldn't rewrite that as <b>{role}</b> — your message was left as it was.")
            return
        if rewritten.strip() == original.strip():
            # A one-word or one-symbol message ("*") comes back as it was. Telegram
            # refuses an edit that changes nothing, which used to fall through to
            # "send it again, delete the original" — the same text, resent.
            _outcome("rewrite", role, chat_id, "unchanged")
            await _give_up(channel, chat_id, bcid, placeholder, owner_id,
                           f"↩️ <b>{role}</b> had nothing to change in that one — it was left as it was.")
            return
        state["applying"] = True
        mid = target.get("message_id")
        if (target.get("text") or "").strip():
            done = await _edit_text(channel, chat_id, bcid, mid, rewritten)
            if not done:
                parent = (target.get("reply_to_message") or {}).get("message_id")
                done = await _send(channel, chat_id, bcid, rewritten, reply_to=parent)
                if done:
                    await _delete(channel, bcid, chat_id, mid)
        else:
            done = await _edit_caption(channel, chat_id, bcid, mid, rewritten)
        if placeholder is not None:
            await _delete(channel, bcid, chat_id, placeholder)
        if not done:
            _outcome("rewrite", role, chat_id, "edit and send refused")
            await _notify_owner(channel, owner_id,
                                f"⚠️ The <b>{role}</b> version could not be applied — your message was left as it was.")
            return
        _outcome("rewrite", role, chat_id, "rewritten in place")
    except Exception:  # noqa: BLE001
        logger.warning("role rewrite background task failed", exc_info=True)


async def _run_role_reply(
    channel: Any, chat_id: int, bcid: str | None, target: dict, role: str, lang: str,
    reply_to: int | None, owner_id: int | None, placeholder: Any = None,
) -> None:
    state: dict[str, bool] = {}
    await _within_deadline(
        "role reply", role, channel, chat_id, bcid, placeholder, owner_id,
        f"⚠️ <b>{role}</b> took too long — nothing was sent.",
        _role_reply(channel, chat_id, bcid, target, role, lang, reply_to, owner_id,
                    placeholder, state),
        state,
    )


async def _role_reply(
    channel: Any, chat_id: int, bcid: str | None, target: dict, role: str, lang: str,
    reply_to: int | None, owner_id: int | None, placeholder: Any, state: dict,
) -> None:
    """Answer *target* once, in role — edited into the owner's placeholder when there
    is one, sent as a threaded reply otherwise. Owner-triggered: no artificial delay,
    which was about half the wait (~16 s of fake typing on a 180-character reply)."""
    try:
        incoming = await _message_as_text(channel, target)
        if not incoming:
            _outcome("role reply", role, chat_id, "unreadable")
            await _give_up(channel, chat_id, bcid, placeholder, owner_id,
                           f"⚠️ Couldn't read that message for <b>{role}</b> — nothing was sent.")
            return
        if placeholder is not None and _has_media(target):
            # 👀 → ✍️ once the photo/voice has been read. Only then: a text message
            # starts on ✍️, and re-sending the same text is refused as "not modified".
            await _edit_text(channel, chat_id, bcid, placeholder, _WRITING)
        context = _recent_context(chat_id, owner_id)
        reply = await _while_typing(
            channel, chat_id, bcid, _generate(role, lang, context, target=incoming),
        )
        if not reply:
            _outcome("role reply", role, chat_id, "generation empty")
            await _give_up(channel, chat_id, bcid, placeholder, owner_id,
                           f"⚠️ Couldn't write a reply as <b>{role}</b> — nothing was sent.")
            return
        state["applying"] = True
        if placeholder is not None and await _edit_text(channel, chat_id, bcid, placeholder, reply):
            _outcome("role reply", role, chat_id, "answered in place")
            return
        if await _send(channel, chat_id, bcid, reply, reply_to=reply_to):
            if placeholder is not None:
                await _delete(channel, bcid, chat_id, placeholder)
            _outcome("role reply", role, chat_id, "sent")
            return
        _outcome("role reply", role, chat_id, "send refused")
        await _give_up(channel, chat_id, bcid, placeholder, owner_id,
                       f"⚠️ The <b>{role}</b> reply could not be sent — Telegram refused it.")
    except Exception:  # noqa: BLE001
        logger.warning("role reply background task failed", exc_info=True)


def _has_content(msg: dict) -> bool:
    """Words, or a photo / video / voice note that can be read into words."""
    if (msg.get("text") or msg.get("caption") or "").strip():
        return True
    try:
        from navig.gateway.channels.telegram_catalog_analyzer import message_media  # noqa: PLC0415

        return message_media(msg) is not None
    except Exception:  # noqa: BLE001
        return False


async def _message_as_text(channel: Any, msg: dict) -> str:
    """The message as a model can read it: its words, plus what its media shows.

    A photo becomes a description and any text in it, a voice note its transcript
    in the language it was spoken, a video both. Asked 2026-09-28: a role "should
    detect images, videos and voice" — it could only answer words.
    """
    words = (msg.get("text") or msg.get("caption") or "").strip()
    seen = None
    try:
        from navig.gateway.channels.telegram_catalog_analyzer import (  # noqa: PLC0415
            message_media,
            understand_message_media,
        )

        if message_media(msg) is not None:
            seen = await understand_message_media(channel, msg)
    except Exception:  # noqa: BLE001 — media is a bonus; the words still answer
        logger.debug("autoreply: media could not be read", exc_info=True)
    if seen and words:
        return f"{words}\n[{seen}]"
    if seen:
        return f"[{seen}]"
    return words


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


#: Characters of the other person's writing shown to the model as "their language".
_LANG_SAMPLE_CHARS = 160


def _language_sample(target: str, context: list[tuple[str, str]], rewrite: bool) -> str:
    """Text whose language the reply must match.

    A rewrite keeps the owner's own message's language. A reply follows how THEY
    write across the conversation, ending with the message being answered: keyed on
    that message alone, a French chat's one-word "punk" got an English answer.
    """
    if rewrite:
        text = target
    else:
        theirs = [t for who, t in context if who == "Them" and t]
        text = " ".join([*theirs[-4:], target])
    flat = " ".join(text.split())
    return flat[-_LANG_SAMPLE_CHARS:]


async def _generate(
    role: str, lang: str, context: list[tuple[str, str]], *, target: str | None = None,
    rewrite: bool = False,
) -> str:
    # A name that resolves to nothing keeps the old behaviour — pro mode has always
    # accepted any word as a style hint. Only the per-message trigger is strict.
    persona = resolve_role(role) or (
        f"Reply as the account owner in the persona and style of '{role}'."
    )
    # No language named → the language of the message being answered. Keyed on
    # THAT message rather than "the other person": a conversation can switch, and a
    # voice note's transcript arrives in whatever language it was spoken. Naming
    # one (`tyler fr`) is only for answering in a DIFFERENT language on purpose.
    if lang:
        lang_clause = f" Always write your reply in {lang}."
    elif target and rewrite:
        lang_clause = " Keep the language the owner's message is written in."
    elif target:
        lang_clause = (
            " Reply in the language of the message you are answering; if it has no "
            "words of its own, use the language of the conversation."
        )
    else:
        lang_clause = " Reply in the same language the other person is using."
    # The guardrail floor comes FIRST, and deliberately the MINIMAL one.
    #
    # This is the only surface in the tree that tells the model to speak as a HUMAN
    # BEING rather than as NAVIG, and it writes to THIRD PARTIES — the person messaging
    # the owner, who never agreed to anything. `persona` below is operator-supplied text
    # from `telegram.business.autoreply.roles`, so it is exactly the "identity that can be written by a
    # user" the floor exists to survive; it goes after the floor, never before it.
    #
    # Minimal, not the full block: the minimal floor protects the owner ("never fabricate
    # a path, command, URL or capability", "keep the operator's private data private",
    # "you are not a doctor") WITHOUT requiring AI disclosure, so it does not contradict
    # this feature's stated purpose. The full block's preamble ("These rules come from
    # NAVIG itself…") would both leak the product name into a human-voiced reply and
    # collide with the no-disclosure instruction.
    from navig.agent.conv.guardrails import guardrail_floor_minimal  # noqa: PLC0415

    system = (
        f"{guardrail_floor_minimal()}\n\n"
        "You are replying on the owner's personal Telegram Business account, AS the owner, "
        "to the person messaging them. Never reveal that you are an AI or an assistant. "
        + persona + lang_clause +
        # Length is the persona's call. A blanket "be concise" here overrode Tyler's
        # rant into two lines, and made a rewrite drop all but the first sentence.
        " Keep it natural and human. "
        # This goes out AS the owner, to someone who will act on it. A persona asked
        # for "a solution" will happily invent a delivery price to give one.
        "Never invent facts — no prices, dates, availability, deadlines or promises "
        "that are not in the conversation; when the answer needs one you do not have, "
        "say plainly how they can get it. "
        "Output ONLY the reply text — no quotes, no labels, no preamble."
    )
    convo = "\n".join(f"{who}: {t}" for who, t in context[-_CONTEXT_N:]) or "(no prior messages)"
    if target and rewrite:
        # The owner's OWN message, said again in the persona's voice. Same meaning,
        # nothing added: it replaces what they wrote, so it must still be what they
        # meant — a rewrite that invents a promise has put words in their mouth.
        # Measured 2026-09-29: a multi-paragraph analysis (a confidence line, a list
        # of clues, a conclusion) came back as its FIRST SENTENCE — the rest of the
        # owner's message was simply gone. "Keep what it means" was not enough.
        user = (
            f"Conversation so far:\n{convo}\n\nThe owner wrote:\n{target}\n\n"
            "Rewrite that message in your voice. Keep ALL of it — every point, every "
            "list item, every line — in the same order and structure, at roughly the "
            "same length — but say EACH line again in your own voice, word choice and "
            "attitude, not the original wording with a word swapped. No summarising, "
            "no cutting, and no added facts, promises or details. Your usual length and closing-line "
            "habits do not apply here — this is their message. Output only the "
            "rewritten message:"
        )
    elif target:
        user = (
            f"Conversation so far:\n{convo}\n\nThey wrote:\n{target}\n\n"
            "Write your reply to that message, as the owner:"
        )
    else:
        user = f"Conversation so far:\n{convo}\n\nWrite your next reply as the owner:"
    if target and not lang:
        # LAST, where even a small model obeys it. Stated only in the system prompt,
        # a local 7B model answered a Russian message in English after a long
        # English persona text — the most recent language it had read.
        user += f"\n(Write it in the same language as this: «{_language_sample(target, context, rewrite)}»)"
    try:
        from navig.llm.generate import llm_generate

        choice = _model_choice()
        submitted = time.monotonic()
        started: list[float] = []

        def _call() -> str:
            started.append(time.monotonic())
            return llm_generate(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                timeout=60.0, **choice,
            )

        out = await asyncio.to_thread(_call)
        done = time.monotonic()
        # Timings only, never content: "waited" is the time before a worker thread
        # picked the call up — the stall a busy daemon hides.
        logger.info(
            "role generation: model=%s waited=%.1fs model_time=%.1fs chars=%d",
            choice.get("model_override") or choice.get("mode"),
            (started[0] if started else done) - submitted,
            done - (started[0] if started else submitted), len(out or ""),
        )
        return (out or "").strip()
    except Exception:  # noqa: BLE001
        logger.warning("autoreply generation failed", exc_info=True)
        return ""


# ── human-like sending (typing indicator + realistic delays) ─────────────────


#: A persona has to SOUND like someone, which a flat, cautious sampling cannot do.
_VOICE_TEMPERATURE = 0.9


def _model_choice() -> dict[str, Any]:
    """Which model writes a persona reply.

    It used ``mode="chat"``, which on this operator's router is a 3-billion-parameter
    local model (`ollama:qwen2.5:3b-instruct`): whatever the prompt said, Tyler came
    out as "Здравствуйте, какое удовольствие вас слышать!". The default is now the
    router's ``big`` tier — still the operator's own choice of model, still local if
    that is what they configured. ``telegram.business.autoreply.model``
    (`provider:model`) picks any other; a cloud model writes a far more convincing
    character, but it sends the conversation to that provider, which is the
    operator's decision to make, not this code's.
    """
    try:
        chosen = str(_cfg().get(CFG_MODEL, "") or "").strip()
    except Exception:  # noqa: BLE001
        chosen = ""
    if chosen:
        return {"model_override": chosen, "temperature": _VOICE_TEMPERATURE}
    return {"mode": "big", "temperature": _VOICE_TEMPERATURE}


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


async def _human_send(
    channel: Any, chat_id: int, bcid: str | None, incoming: str, reply: str,
    *, reply_to: int | None = None,
) -> bool:
    """Reading pause → typing indicator held for a length-proportional time → send.

    Returns whether Telegram accepted the message."""
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
    return await _send(channel, chat_id, bcid, reply, reply_to=reply_to)


async def _chat_action(channel: Any, chat_id: int, bcid: str | None) -> None:
    try:
        data: dict[str, Any] = {"chat_id": chat_id, "action": "typing"}
        if bcid:
            data["business_connection_id"] = bcid
        await channel._api_call("sendChatAction", data)
    except Exception:  # noqa: BLE001
        pass


async def _send(
    channel: Any, chat_id: int, bcid: str | None, text: str, *, reply_to: int | None = None,
) -> bool:
    """Send as the business account. True only when Telegram accepted it — the
    channel's `_api_call` reports a refusal by returning None, not by raising."""
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if bcid:
        data["business_connection_id"] = bcid
    if reply_to is not None:
        # Threaded under the message it answers. If that message was deleted in the
        # meantime the answer still goes out, unthreaded, rather than being lost.
        data["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
    try:
        return bool(await channel._api_call("sendMessage", data))
    except Exception:  # noqa: BLE001
        logger.debug("autoreply send failed", exc_info=True)
        return False


async def _delete(channel: Any, bcid: str | None, chat_id: int | None, message_id: int | None) -> None:
    if message_id is None:
        return
    try:
        if bcid:
            from navig.telegram.deletions import note_own_deletion  # noqa: PLC0415

            note_own_deletion(bcid, [message_id])  # our trigger — keep it out of the digest
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
