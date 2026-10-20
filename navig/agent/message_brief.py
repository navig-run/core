"""
navig.agent.message_brief — ONE decision-model (Clef) call per incoming Telegram message.

A plain-text Telegram message passes through a chain of separate classifiers, each with
its own regex or keyword list: system intent, mode (talk / reason / act / code), depth,
settings change, space switch, reminder. A brief asks Clef ALL of those questions about
the message in a single call (~40-200 ms, up to 64 questions), so each classifier can be
measured against the same reading of the message.

How it runs — Telegram updates are processed ONE AT A TIME, so nothing here may make the
bot wait:

1. :func:`start` is called once per message, as soon as the text is final. It schedules
   the Clef call as a background task and returns immediately.
2. Each classifier that already exists calls :func:`note` with the answer it produced.
3. When both sides of a question are in (in either order), :func:`navig.agent.decide.compare`
   records whether Clef agreed. ``navig decider status`` shows the agreement rate and
   ``navig decider review`` the disagreements.

The use is ``telegram_brief`` and it is **shadow-only**: no classifier acts on Clef's
answer yet. A question graduates to live one at a time, once its agreement — and the
disagreements a human has read — justify it.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from navig.agent import decide

logger = logging.getLogger(__name__)

USE = "telegram_brief"

#: Briefs kept at once (one per recent message). Oldest evicted first.
MAX_BRIEFS = 256
#: A brief older than this is dropped: its message is long done.
BRIEF_TTL_S = 300.0

# ── the questions ────────────────────────────────────────────

MODES = {
    "TALK": "conversation, a quick question or chit-chat — answer directly",
    "REASON": "needs analysis, explanation, comparison or careful thought",
    "ACT": "asks to look something up or do something on the web: search, fetch, browse",
    "CODE": "about writing, fixing, reviewing or explaining code",
}
DEPTHS = {
    "quick": "a short, direct answer is enough",
    "deep": "needs a thorough, multi-part or well-researched answer",
}
SYSTEM_INTENTS = {
    "disk": "asks about disk space or storage usage of this machine",
    "memory": "asks about RAM / memory usage of this machine",
    "cpu": "asks about CPU load or usage of this machine",
    "services": "asks which services are running on this machine",
    "ports": "asks which network ports are open or listening on this machine",
    "none": "none of these — not a request for this machine's system status",
}
SPACE_INTENTS = {
    "switch": "asks to switch to (open, go to, use) one of their project spaces",
    "intake": "asks to plan one of their project spaces: its roadmap, goals or next phase",
    "none": "neither — not about switching or creating a space",
}


def questions() -> list[decide.Question]:
    """The brief's questions. Ids are what :func:`note` callers pass as *qid*."""
    return [
        decide.choice("mode", "What kind of request is this message?", MODES),
        decide.choice("depth", "How much answer does this message need?", DEPTHS),
        decide.choice("system_intent", "Does this ask about this computer's system status?", SYSTEM_INTENTS),
        decide.choice("space_intent", "Is this about the user's project spaces?", SPACE_INTENTS),
        decide.yesno("reminder", "Does the user ask to be reminded of something at a later time?"),
        decide.yesno(
            "settings_intent",
            "Does the user ask to change one of the assistant's own settings (its voice, "
            "persona, focus mode, model tier or text-to-speech)?",
        ),
    ]


# ── per-message state ────────────────────────────────────────


@dataclass
class _Brief:
    created: float
    text: str
    outcome: decide.Outcome | None = None
    done: bool = False
    pending: dict[str, str] = field(default_factory=dict)  # legacy answers waiting for Clef
    noted: set[str] = field(default_factory=set)  # one comparison per question per message


_lock = threading.Lock()
_briefs: OrderedDict[tuple[Any, Any], _Brief] = OrderedDict()


def _key(chat_id: Any, message_id: Any) -> tuple[Any, Any] | None:
    if chat_id is None or message_id is None:
        return None
    return (str(chat_id), str(message_id))


def _evict(now: float) -> None:
    while _briefs:
        k, b = next(iter(_briefs.items()))
        if len(_briefs) > MAX_BRIEFS or now - b.created > BRIEF_TTL_S:
            _briefs.pop(k, None)
        else:
            break


def start(chat_id: Any, message_id: Any, text: str) -> bool:
    """Begin the brief for one message — in the background. Returns at once. Never raises.

    Returns True when a brief was scheduled (the use is active and the message is new).
    """
    try:
        key = _key(chat_id, message_id)
        if key is None or not (text or "").strip() or not decide.is_active(USE):
            return False
        now = time.monotonic()
        with _lock:
            _evict(now)
            if key in _briefs:
                return False
            _briefs[key] = _Brief(created=now, text=text)

        def _work() -> None:
            out = decide.outcome(text, questions(), use=USE)
            with _lock:
                b = _briefs.get(key)
                if b is None:
                    return
                b.outcome = out
                b.done = True
                waiting = dict(b.pending)
                b.pending.clear()
            for qid, legacy in waiting.items():
                decide.compare(out, qid, legacy, sample=text)

        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(target=_work, name="message-brief", daemon=True).start()
            return True
        from navig.core.background import spawn

        spawn(asyncio.to_thread(_work), name="message-brief")
        return True
    except Exception:  # noqa: BLE001 — a brief must never touch the message path
        logger.debug("message_brief: start failed", exc_info=True)
        return False


def note(chat_id: Any, message_id: Any, qid: str, legacy: Any) -> None:
    """Record the answer an existing classifier made for this message. Never raises.

    Compared with Clef's answer now if it has arrived, or as soon as it does. A message
    with no brief (the use is off, or it was a slash command) is ignored.
    """
    try:
        key = _key(chat_id, message_id)
        if key is None:
            return
        legacy_s = str(legacy)
        with _lock:
            b = _briefs.get(key)
            if b is None or qid in b.noted:
                return  # no brief, or this question was already decided for this message
            b.noted.add(qid)
            if not b.done:
                b.pending[qid] = legacy_s
                return
            out, text = b.outcome, b.text
        decide.compare(out, qid, legacy_s, sample=text)
    except Exception:  # noqa: BLE001
        logger.debug("message_brief: note failed", exc_info=True)


def reset_for_tests() -> None:
    with _lock:
        _briefs.clear()
