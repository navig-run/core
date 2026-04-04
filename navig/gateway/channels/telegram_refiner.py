"""
Telegram Refinement Engine — navig gateway channel module.

Runs an interactive clarification + refinement loop:
  1. User sends text (or hits ♻️ Refine from a card)
  2. LLM generates 3 targeted clarifying questions
  3. Bot asks Q1 → user answers → Q2 → user answers → Q3 → user answers
  4. Confirmation keyboard: "Looks good, refine it!" / "Let me re-answer"
  5. LLM refines original text with all 3 answers
  6. Diff‑style summary sent (key changes highlighted)

Callback protocol:
  rfn:yes:<KEY>       — confirm & trigger refinement
  rfn:edit:<KEY>      — re-answer last question
  rfn:accept:<KEY>    — accept refined text (dismiss)
  rfn:rerefine:<KEY>  — run another refinement round
  rfn:revert:<KEY>    — revert to original text

Usage::

    from navig.gateway.channels.telegram_refiner import RefinementEngine

    refiner = RefinementEngine(channel)
    await refiner.start(chat_id, user_id, text, topic="fight club analysis")
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


def _mdv2_escape(text: str) -> str:
    return re.sub(r"([_\*\[\]\(\)~`>#+\-=|{}.!\\])", r"\\\1", str(text))

# ─────────────────────────────────────────────────────────────────
# State machine
# ─────────────────────────────────────────────────────────────────


class ClarifyState(str, Enum):
    ASKING = "asking"
    CONFIRMING = "confirming"
    REFINING = "refining"
    DONE = "done"


@dataclass
class ClarifySession:
    """
    Represents one refinement conversation.

    Stored ephemerally in CallbackStore under key ``rfn:<uuid>``.
    Incoming text replies are matched by user_id + chat_id.
    """

    state: str  # ClarifyState value
    original_text: str
    topic: str
    questions: list[str]  # 3 clarifying questions from LLM
    answers: dict[str, str]  # {str(q_index): answer}
    current_q: int  # 0-based question index
    refined_text: str | None
    session_key: str
    chat_id: int
    user_id: int
    message_id: int | None  # message containing current question
    created_at: float = field(default_factory=time.time)

    # ── Serialisation ──

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClarifySession:
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def serialise(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def deserialise(cls, s: str) -> ClarifySession:
        return cls.from_dict(json.loads(s))

    # ── Helpers ──

    @property
    def current_question(self) -> str | None:
        if 0 <= self.current_q < len(self.questions):
            return self.questions[self.current_q]
        return None

    @property
    def all_answered(self) -> bool:
        return len(self.answers) >= len(self.questions)


# ─────────────────────────────────────────────────────────────────
# Keyboard builders
# ─────────────────────────────────────────────────────────────────


def build_confirmation_keyboard(session: ClarifySession) -> list[list[dict]]:
    """Keyboard shown before triggering the LLM refinement call."""
    key = session.session_key
    return [
        [
            {"text": "✅ Refine it!", "callback_data": f"rfn:yes:{key}"},
            {"text": "✏️ Re-answer", "callback_data": f"rfn:edit:{key}"},
        ]
    ]


def build_accept_keyboard(session: ClarifySession) -> list[list[dict]]:
    """Keyboard shown after refined text is ready."""
    key = session.session_key
    return [
        [
            {"text": "✅ Accept", "callback_data": f"rfn:accept:{key}"},
            {"text": "♻️ Refine more", "callback_data": f"rfn:rerefine:{key}"},
            {"text": "⏪ Revert", "callback_data": f"rfn:revert:{key}"},
        ]
    ]


def build_skip_keyboard(session: ClarifySession) -> list[list[dict]]:
    """Shown with each question so users can skip."""
    key = session.session_key
    return [[{"text": "⏭ Skip this question", "callback_data": f"rfn:skip:{key}"}]]


# ─────────────────────────────────────────────────────────────────
# LLM prompt helpers
# ─────────────────────────────────────────────────────────────────

_CLARIFY_PROMPT = """You are a precise assistant helping a user refine their written content.

## Original Content
{text}

## Topic
{topic}

## Task
Generate exactly 3 short, targeted clarifying questions that would most improve this content.
Each question should address a *different* aspect (e.g., scope, tone, missing detail, structure).

Respond with ONLY a JSON array of 3 strings:
["Question 1?", "Question 2?", "Question 3?"]
"""

_REFINE_PROMPT = """You are a precise editor. Refine the content below using the clarifying answers provided.

## Original Content
{text}

## Clarifying Answers
{qa_block}

## Instructions
- Apply all relevant insights from the answers
- Preserve the author's voice and intent
- Return ONLY the refined content (no commentary, no meta-text)
"""

_DIFF_SUMMARY_PROMPT = """Compare these two texts and produce a SHORT change summary (3-5 bullet points max).
Focus on meaningful changes, not minor wording.

ORIGINAL:
{original}

REFINED:
{refined}

Respond with a concise bullet list only.
"""


def _build_qa_block(session: ClarifySession) -> str:
    lines = []
    for i, q in enumerate(session.questions):
        ans = session.answers.get(str(i), "(skipped)")
        lines.append(f"Q{i + 1}: {q}\nA{i + 1}: {ans}")
    return "\n\n".join(lines)


# ─────────────────────────────────────────────────────────────────
# RefinementEngine
# ─────────────────────────────────────────────────────────────────


class RefinementEngine:
    """
    Orchestrates the clarification → refinement state machine.

    Parameters
    ----------
    channel:
        The ``TelegramChannel`` instance (must have ``send_message``,
        ``edit_message``, ``answer_callback_query``).
    cb_store:
        A ``CallbackStore`` instance.  If not provided, retrieved from
        ``telegram_keyboards.get_callback_store()``.
    """

    _SESSION_TTL = 3600 * 4  # 4 hours

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
            logger.error("RefinementEngine: cannot get CallbackStore: %s", exc)
            raise

    def _get_llm(self) -> Any:
        """Return navig LLM router (lazy)."""
        try:
            from navig.agent.router import get_router

            return get_router()
        except Exception:
            try:
                from navig.agent import get_llm

                return get_llm()
            except Exception as exc:
                logger.error("RefinementEngine: cannot get LLM: %s", exc)
                raise

    # ── Public API ───────────────────────────────────────────────

    async def start(
        self,
        chat_id: int,
        user_id: int,
        text: str,
        topic: str = "",
    ) -> ClarifySession:
        """
        Begin a new refinement session.

        Calls LLM to generate 3 clarifying questions, then sends Q1.
        """
        session_key = f"rfn:{uuid.uuid4().hex[:12]}"

        # Send "thinking" message
        thinking_msg = await self.channel.send_message(
            chat_id, "♻️ _Generating clarifying questions…_", parse_mode="MarkdownV2"
        )
        thinking_id = getattr(thinking_msg, "message_id", None)
        if isinstance(thinking_msg, dict):
            thinking_id = thinking_msg.get("message_id")

        questions = await self._generate_questions(text, topic)

        session = ClarifySession(
            state=ClarifyState.ASKING.value,
            original_text=text,
            topic=topic,
            questions=questions,
            answers={},
            current_q=0,
            refined_text=None,
            session_key=session_key,
            chat_id=chat_id,
            user_id=user_id,
            message_id=thinking_id,
        )

        store = self._get_store()
        store.put(session_key, {"session": session.serialise()}, ttl=self._SESSION_TTL)
        # Register pending text reply expectation
        store.put(
            f"rfn_pending:{user_id}:{chat_id}",
            {"session_key": session_key},
            ttl=self._SESSION_TTL,
        )

        await self._ask_question(session)
        return session

    async def receive_answer(
        self,
        user_id: int,
        chat_id: int,
        answer_text: str,
    ) -> bool:
        """
        Handle a free-text reply from a user who is mid-session.

        Returns ``True`` if the reply was consumed, ``False`` if no active
        session was found for this user/chat.
        """
        store = self._get_store()
        pending = store.get(f"rfn_pending:{user_id}:{chat_id}")
        if not pending:
            return False

        session_key = pending.get("session_key", "")
        entry = store.get(session_key)
        if not entry:
            store.remove(f"rfn_pending:{user_id}:{chat_id}")
            return False

        session = ClarifySession.deserialise(entry["session"])
        if session.state != ClarifyState.ASKING.value:
            return False

        # Record answer
        session.answers[str(session.current_q)] = answer_text
        session.current_q += 1

        if session.all_answered or session.current_q >= len(session.questions):
            # Move to confirmation
            session.state = ClarifyState.CONFIRMING.value
            store.put(session_key, {"session": session.serialise()}, ttl=self._SESSION_TTL)
            await self._send_confirmation(session)
        else:
            store.put(session_key, {"session": session.serialise()}, ttl=self._SESSION_TTL)
            await self._ask_question(session)

        return True

    async def refine(self, session: ClarifySession) -> None:
        """Run the LLM refinement call and send the result."""
        session.state = ClarifyState.REFINING.value
        store = self._get_store()
        store.put(session.session_key, {"session": session.serialise()}, ttl=self._SESSION_TTL)

        await self.channel.send_message(
            session.chat_id,
            "⚙️ _Refining with your context…_",
            parse_mode="MarkdownV2",
        )

        qa_block = _build_qa_block(session)
        prompt = _REFINE_PROMPT.format(text=session.original_text, qa_block=qa_block)

        try:
            llm = self._get_llm()
            refined = await self._call_llm(llm, prompt)
        except Exception as exc:
            logger.error("RefinementEngine.refine LLM error: %s", exc)
            await self.channel.send_message(session.chat_id, "❌ LLM error during refinement.")
            return

        session.refined_text = refined
        session.state = ClarifyState.DONE.value
        store.put(session.session_key, {"session": session.serialise()}, ttl=self._SESSION_TTL)
        # Clear pending text listener
        store.remove(f"rfn_pending:{session.user_id}:{session.chat_id}")

        # Send refined text
        keyboard = build_accept_keyboard(session)
        await self.channel.send_message(
            session.chat_id,
            f"✨ *Refined Result:*\n\n{_mdv2_escape(refined)}",
            parse_mode="MarkdownV2",
            reply_markup={"inline_keyboard": keyboard},
        )

        # Send diff summary
        try:
            diff_prompt = _DIFF_SUMMARY_PROMPT.format(
                original=session.original_text, refined=refined
            )
            summary = await self._call_llm(llm, diff_prompt)
            await self.channel.send_message(
                session.chat_id,
                f"📋 *Changes made:*\n\n{_mdv2_escape(summary)}",
                parse_mode="MarkdownV2",
            )
        except Exception as exc:
            logger.warning("RefinementEngine: diff summary failed: %s", exc)

    # ── Internal ─────────────────────────────────────────────────

    async def _generate_questions(self, text: str, topic: str) -> list[str]:
        prompt = _CLARIFY_PROMPT.format(text=text, topic=topic or "general")
        try:
            llm = self._get_llm()
            raw = await self._call_llm(llm, prompt)
            # Parse JSON array from response
            import re

            match = re.search(r"\[.*?\]", raw, re.DOTALL)
            if match:
                questions = json.loads(match.group(0))
                if isinstance(questions, list) and questions:
                    return [str(q) for q in questions[:3]]
        except Exception as exc:
            logger.warning("RefinementEngine: LLM question generation failed: %s", exc)

        # Fallback questions
        return [
            "What is the primary goal or audience for this content?",
            "Are there any key points you feel are missing or underdeveloped?",
            "What tone or style should the final output have?",
        ]

    async def _call_llm(self, llm: Any, prompt: str) -> str:
        """Thin wrapper — handles both sync and async LLM clients."""
        try:
            if hasattr(llm, "acomplete"):
                result = await llm.acomplete(prompt)
                return str(result)
            if hasattr(llm, "complete"):
                import asyncio

                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(None, llm.complete, prompt)
                return str(result)
            if hasattr(llm, "agenerate"):
                result = await llm.agenerate(prompt)
                return str(result)
            if callable(llm):
                return str(llm(prompt))
        except Exception as exc:
            logger.error("RefinementEngine._call_llm: %s", exc)
            raise
        return ""

    async def _ask_question(self, session: ClarifySession) -> None:
        """Send the current question to the user."""
        q = session.current_question
        if q is None:
            return
        total = len(session.questions)
        q_num = session.current_q + 1
        keyboard = build_skip_keyboard(session)
        text = (
            f"🤔 *Question {q_num}/{total}*\n\n"
            f"{_mdv2_escape(q)}\n\n"
            r"_Reply with your answer, or tap Skip\._"
        )
        msg = await self.channel.send_message(
            session.chat_id,
            text,
            parse_mode="MarkdownV2",
            reply_markup={"inline_keyboard": keyboard},
        )
        if isinstance(msg, dict):
            session.message_id = msg.get("message_id")

    async def _send_confirmation(self, session: ClarifySession) -> None:
        """Send the Q&A summary with confirm/re-answer keyboard."""
        lines = ["✅ *Your answers:*\n"]
        for i, q in enumerate(session.questions):
            ans = session.answers.get(str(i), "_(skipped)_")
            lines.append(
                f"*Q{i + 1}:* {_mdv2_escape(q)}\n"
                f"*A:* {_mdv2_escape(ans)}\n"
            )
        lines.append("_Ready to refine?_")
        text = "\n".join(lines)
        keyboard = build_confirmation_keyboard(session)
        await self.channel.send_message(
            session.chat_id,
            text,
            parse_mode="MarkdownV2",
            reply_markup={"inline_keyboard": keyboard},
        )


# ─────────────────────────────────────────────────────────────────
# Callback handler
# ─────────────────────────────────────────────────────────────────


async def handle_rfn_callback(
    channel: Any,
    callback_query: Any,
    cb_store: Any,
) -> None:
    """
    Dispatch ``rfn:*`` callback data.

    Expected formats:
      ``rfn:yes:<KEY>``       — confirm & run refinement
      ``rfn:edit:<KEY>``      — re-answer last question
      ``rfn:accept:<KEY>``    — accept refined text, clear session
      ``rfn:rerefine:<KEY>``  — start new round with refined text as base
      ``rfn:revert:<KEY>``    — send original text back
      ``rfn:skip:<KEY>``      — skip current question
    """
    cb_id = getattr(callback_query, "id", None) or ""
    cb_data: str = getattr(callback_query, "data", "") or ""

    parts = cb_data.split(":", 2)
    if len(parts) < 3:
        return
    action = parts[1]
    session_key = parts[2]

    entry = cb_store.get(session_key)
    if not entry:
        try:
            await channel.answer_callback_query(cb_id, "⚠️ Session expired")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        return

    try:
        session = ClarifySession.deserialise(entry.get("session", "{}"))
    except Exception as exc:
        logger.warning("handle_rfn_callback deserialise error: %s", exc)
        await channel.answer_callback_query(cb_id, "⚠️ Session error")
        return

    engine = RefinementEngine(channel, cb_store)

    if action == "yes":
        await channel.answer_callback_query(cb_id, "♻️ Refining…")
        await engine.refine(session)

    elif action == "edit":
        # Step back one question
        if session.current_q > 0:
            session.current_q -= 1
            key_str = str(session.current_q)
            session.answers.pop(key_str, None)
            session.state = ClarifyState.ASKING.value
            cb_store.put(session_key, {"session": session.serialise()}, ttl=engine._SESSION_TTL)
            # Re-register pending text listener
            cb_store.put(
                f"rfn_pending:{session.user_id}:{session.chat_id}",
                {"session_key": session_key},
                ttl=engine._SESSION_TTL,
            )
            await engine._ask_question(session)
        await channel.answer_callback_query(cb_id, "✏️ Re-asking…")

    elif action == "skip":
        # Treat skip as empty answer then advance
        session.answers[str(session.current_q)] = ""
        session.current_q += 1
        if session.all_answered or session.current_q >= len(session.questions):
            session.state = ClarifyState.CONFIRMING.value
            cb_store.put(session_key, {"session": session.serialise()}, ttl=engine._SESSION_TTL)
            await engine._send_confirmation(session)
        else:
            cb_store.put(session_key, {"session": session.serialise()}, ttl=engine._SESSION_TTL)
            await engine._ask_question(session)
        await channel.answer_callback_query(cb_id, "⏭ Skipped")

    elif action == "accept":
        refined = session.refined_text or session.original_text
        try:
            await channel.send_message(
                session.chat_id,
                f"✅ *Accepted:*\n\n{_mdv2_escape(refined)}",
                parse_mode="MarkdownV2",
            )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        cb_store.remove(session_key)
        cb_store.remove(f"rfn_pending:{session.user_id}:{session.chat_id}")
        await channel.answer_callback_query(cb_id, "✅ Accepted")

    elif action == "rerefine":
        base = session.refined_text or session.original_text
        await channel.answer_callback_query(cb_id, "♻️ Starting new round…")
        await engine.start(
            chat_id=session.chat_id,
            user_id=session.user_id,
            text=base,
            topic=session.topic,
        )
        cb_store.remove(session_key)

    elif action == "revert":
        await channel.send_message(
            session.chat_id,
            f"⏪ *Original text:*\n\n{_mdv2_escape(session.original_text)}",
            parse_mode="MarkdownV2",
        )
        cb_store.remove(session_key)
        cb_store.remove(f"rfn_pending:{session.user_id}:{session.chat_id}")
        await channel.answer_callback_query(cb_id, "⏪ Reverted")

    else:
        await channel.answer_callback_query(cb_id, "")
