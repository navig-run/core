"""read_aloud — "read this to me", from the agent.

The natural-language door to the bedtime reader (the ``/read`` command is the deterministic
one — see ``navig.telegram.read_actions``). The engine is the ``navig-audio`` plugin; this
tool is only registered when that plugin is installed.

It is built around two facts about reading a text aloud:

* **It takes minutes**, far longer than a tool call may. So the tool *starts* a background
  reading and returns at once; the parts reach the chat as they are made, through the same
  delivery seam ``chat_send_file`` uses, so the recipient is always the conversation's own
  chat and a rejected send is reported as a failure.
* **It costs real money per character.** A big text is not started: the tool returns the
  quote and says so, the model asks the operator, and only an explicit ``confirmed`` call
  starts it — which needs no text re-sent (the text is held server-side for a while), because
  re-emitting 20,000 characters through a model to say "yes" would be absurd.

The same job key as ``/read`` is used, so ``/read stop`` stops a reading the agent started.
"""

from __future__ import annotations

import logging
from typing import Any

from navig.agent.tools.chat_media_tools import deliver_to_session, say_to_session, split_session
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


class ReadAloudTool(BaseTool):
    """Read a text aloud to the operator, as audio parts in this chat."""

    name = "read_aloud"
    needs_session = True
    description = (
        "Read a text aloud to the operator like a book: quiet audio parts of about ten minutes "
        "sent to THIS chat as they are made (the first within a minute). Use it when they ask "
        "to be read to / 'read this to me' / 'почитай мне' / 'lis-moi ça' and give the text — "
        "pass the text EXACTLY as given, never shortened or summarised. Returns at once; do not "
        "wait for the audio. Costs one provider credit per character: a big text is NOT started — "
        "you get the quote; ask the operator, then call again with confirmed=true (no text "
        "needed). For a text longer than a few thousand characters, tell the operator to send it "
        "as a .txt file with the caption /read instead of pasting it. action='stop' cancels a "
        "reading in progress."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = [
        {"name": "text", "type": "string", "required": False,
         "description": "The text to read, verbatim. Omit when confirming a quoted reading."},
        {"name": "confirmed", "type": "boolean", "required": False,
         "description": "true = the operator agreed to the quoted cost (starts the pending reading)."},
        {"name": "voice", "type": "string", "required": False,
         "description": "A voice id or the name of one of the operator's voices. Default: their configured one."},
        {"name": "language", "type": "string", "required": False,
         "description": "en | ru | fr. Default: detected from the text."},
        {"name": "action", "type": "string", "required": False,
         "description": "'stop' to cancel the reading in progress. Default: read."},
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        session_id = str(args.get("_session_id") or "")
        routed = split_session(session_id)
        if routed is None:
            return self._fail(
                "This conversation has no chat address, so there is nowhere to send the audio. "
                "Ask from Telegram (or another connected channel).",
                "no chat session",
            )
        try:
            from navig_audio import reader, reading_job
        except ImportError:
            return self._fail("Reading aloud needs the navig-audio plugin (pip install navig-audio).", "plugin missing")

        key = f"{routed[0]}:{routed[1]}"  # == read_actions.job_key(chat_id) for Telegram

        if str(args.get("action") or "").strip().lower() == "stop":
            if reading_job.stop(key):
                return ToolResult(name=self.name, success=True, output="Stopping after the part in progress.")
            if reading_job.discard_pending(key):
                return ToolResult(name=self.name, success=True, output="Dropped the reading that was waiting for confirmation. Nothing was spent.")
            return ToolResult(name=self.name, success=True, output="Nothing is being read right now.")

        async def deliver(part: Any) -> bool:
            result = await deliver_to_session(session_id, part.path, f"📖 {part.caption}")
            return result.ok

        async def notify(event: Any) -> None:
            from navig.telegram.read_actions import describe_event

            text = describe_event(event)
            if text:
                await say_to_session(session_id, text)

        text = str(args.get("text") or "")
        confirmed = bool(args.get("confirmed"))
        try:
            if confirmed and not text.strip():
                result = await reading_job.confirm(key, deliver=deliver, notify=notify)
            else:
                options = reader.options_from_config(
                    voice=str(args.get("voice") or "").strip() or None,
                    lang=str(args.get("language") or "").strip().lower() or None,
                )
                result = await reading_job.begin(
                    key, text, options, deliver=deliver, notify=notify, confirmed=confirmed
                )
        except reader.ReadError as exc:
            return self._fail(str(exc), "invalid request")

        return self._describe(result, reading_job.Outcome)

    # ── results, phrased for the MODEL (it speaks to the operator in their language) ──

    def _fail(self, message: str, error: str) -> ToolResult:
        return ToolResult(name=self.name, success=False, output=message, error=error)

    def _describe(self, result: Any, Outcome: Any) -> ToolResult:  # noqa: N803 — an enum class
        plan, o = result.plan, result.outcome
        if o is Outcome.STARTED:
            return ToolResult(
                name=self.name,
                success=True,
                output=(
                    f"Started. {plan.part_count} part(s), about {round(plan.minutes)} min, "
                    f"{plan.chars:,} characters. The parts arrive in this chat as they are made "
                    "— tell the operator it has begun and stop; do not wait. They can say "
                    "'stop' (or /read stop) to cancel."
                ),
            )
        if o is Outcome.NEEDS_CONFIRM:
            return ToolResult(
                name=self.name,
                success=True,
                output=(
                    f"NOT started — it is long: {plan.chars:,} characters, about "
                    f"{round(plan.minutes)} min, about {result.spend:,} provider credits. Ask the "
                    "operator whether to go ahead. If yes, call read_aloud again with "
                    "confirmed=true and NO text (the text is held). If no, call it with action='stop'."
                ),
            )
        if o is Outcome.INSUFFICIENT:
            return self._fail(
                f"Not started: it needs about {result.spend:,} credits and the account has "
                f"{result.remaining:,} left. Suggest a shorter text.",
                "insufficient credits",
            )
        if o is Outcome.BUSY:
            return self._fail(
                "Already reading to this chat. Call read_aloud with action='stop' first, or let it finish.",
                "busy",
            )
        if o is Outcome.NOTHING_PENDING:
            return self._fail(
                "Nothing is waiting for confirmation (it may have expired). Send the text again.",
                "nothing pending",
            )
        if o is Outcome.EMPTY:
            return self._fail(
                result.detail or "There is no readable text. Pass the text to read.", "no text"
            )
        return self._fail(result.detail or "Reading could not start.", "unavailable")


def _plugin_installed() -> bool:
    import importlib.util

    return importlib.util.find_spec("navig_audio") is not None


def register_read_aloud_tools() -> None:
    """Register ``read_aloud`` (core toolset, offered every turn) — only when navig-audio is
    installed, so a machine without the plugin never shows the model a tool that cannot work."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(ReadAloudTool(), toolset="core", check_fn=_plugin_installed)
    logger.debug("Read-aloud tool registered: read_aloud")
