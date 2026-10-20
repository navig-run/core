"""chat_send_file — hand a generated file back to the chat it came from.

The missing delivery half of every generative flow: the agent could run
``navig audio gen`` (or write any artifact) but had no way to put the result
into the Telegram conversation — there was no chat-send tool at all, only the
prompt-library's command-scoped ``send_document``.

The routing travels with the conversation, not with the model: the agent loop
injects ``_session_id`` (the chat-stable session key, e.g.
``telegram:user:12345``) into the args of any tool declaring
``needs_session`` — invisible to the LLM, uns spoofable. Delivery goes through
the messaging adapter registry (the same seam Studio uses), so receipts are
honest: "delivered" is only reported when the transport actually accepted the
send.

The recipient is always the chat the conversation belongs to — the operator's
own chat. The tool cannot target arbitrary chats; that is deliberate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)

#: Telegram's bot-upload ceiling (50 MB). Above it the Bot API refuses the
#: upload, so refusing UP FRONT is the difference between an honest error and
#: a mid-send failure the model might retry into a loop.
MAX_SEND_BYTES = 50 * 1024 * 1024

#: Extension → attachment kind understood by the adapter's dispatcher
#: (``telegram_adapter._dispatch_attachment``). ``audio`` is a track, not a voice note:
#: the adapter sends .mp3/.m4a through ``sendAudio`` (Telegram's in-chat player) and any
#: other container as a document, because the Bot API would reject those as audio;
#: ``send_voice`` is never used — it would re-encode the track as a spoken voice note.
_KIND_BY_EXT: dict[str, str] = {
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
    ".webp": "photo",
    ".gif": "animation",
    ".mp4": "video",
    ".mov": "video",
    ".mkv": "video",
    ".webm": "video",
    ".mp3": "audio",
    ".m4a": "audio",
    ".wav": "audio",
    ".flac": "audio",
    ".ogg": "audio",
    ".opus": "audio",
    ".aac": "audio",
}


def attachment_kind(path: str) -> str:
    """Attachment kind for *path* — ``document`` for anything unknown."""
    from pathlib import Path

    return _KIND_BY_EXT.get(Path(path).suffix.lower(), "document")


def split_session(session_id: str) -> tuple[str, str] | None:
    """``(channel, thread_id)`` from a session key — e.g. ``("telegram", "12345")``.

    ``None`` when the key carries no usable address (the CLI's default session
    has no chat to deliver to, and saying so beats guessing).
    """
    parts = [p for p in str(session_id or "").split(":") if p]
    if len(parts) < 2:
        return None
    channel, thread = parts[0], parts[-1]
    if not thread:
        return None
    if channel == "telegram":
        # The adapter does int(thread_id) OUTSIDE its try-block, so an
        # unparseable id would raise from inside a delivery call. Validate here,
        # where an error is a clean tool result instead of a crash.
        try:
            int(thread)
        except ValueError:
            return None
    return channel, thread


@dataclass(frozen=True)
class Delivery:
    """The outcome of handing one file to a conversation's chat.

    ``error`` is the short machine reason (``no chat session`` / ``channel adapter
    unavailable`` / ``delivery failed``); ``detail`` is the sentence a person or a model
    reads. Honest by construction: ``ok`` is True only when the transport accepted the send.
    """

    ok: bool
    error: str = ""
    detail: str = ""
    channel: str = ""
    thread: str = ""
    message_id: str | None = None


async def deliver_to_session(session_id: str, path: Path, caption: str = "") -> Delivery:
    """Send *path* to the chat that *session_id* belongs to, through the messaging adapter.

    The one delivery seam shared by ``chat_send_file`` and the bedtime reader's background
    job, so both report failure the same honest way (a rejected send is a failure, never a
    silent success) and neither can address a chat other than the conversation's own.
    """
    routed = split_session(session_id)
    if routed is None:
        return Delivery(
            ok=False,
            error="no chat session",
            detail=(
                "This conversation has no chat address, so there is nowhere to "
                "deliver the file. Start the conversation from Telegram (or "
                "another connected channel) and send it there."
            ),
        )
    channel_name, thread_id = routed

    try:
        from navig.messaging.adapter_registry import get_adapter_registry

        adapter = get_adapter_registry().get(channel_name)
    except Exception as exc:  # noqa: BLE001 — an unreachable registry is not a crash
        adapter = None
        logger.debug("adapter registry lookup failed: %s", exc)
    if adapter is None:
        return Delivery(
            ok=False,
            error="channel adapter unavailable",
            detail=(
                f"No '{channel_name}' messaging adapter is connected (the gateway "
                "must be running with that adapter enabled), so the file was NOT "
                "sent."
            ),
            channel=channel_name,
            thread=thread_id,
        )

    receipt = await adapter.send_message(
        thread_id,
        caption,
        attachments=[
            {"path": str(path), "kind": attachment_kind(str(path)), "filename": Path(path).name}
        ],
    )
    if not getattr(receipt, "ok", False):
        return Delivery(
            ok=False,
            error="delivery failed",
            detail=(
                f"Delivery to {channel_name}:{thread_id} failed: "
                f"{getattr(receipt, 'error', None) or 'transport rejected the send'}"
            ),
            channel=channel_name,
            thread=thread_id,
        )
    return Delivery(
        ok=True,
        channel=channel_name,
        thread=thread_id,
        message_id=getattr(receipt, "message_id", None) or None,
    )


async def say_to_session(session_id: str, text: str) -> bool:
    """Post a plain text message into the chat *session_id* belongs to. True if it went out.

    The companion of :func:`deliver_to_session` for background work that has something to
    tell the operator after the tool call that started it has long returned.
    """
    routed = split_session(session_id)
    if routed is None:
        return False
    channel_name, thread_id = routed
    try:
        from navig.messaging.adapter_registry import get_adapter_registry

        adapter = get_adapter_registry().get(channel_name)
        if adapter is None:
            return False
        receipt = await adapter.send_message(thread_id, text)
    except Exception as exc:  # noqa: BLE001 — a status line must never crash its caller
        logger.debug("say_to_session failed: %s", exc)
        return False
    return bool(getattr(receipt, "ok", False))


class ChatSendFileTool(BaseTool):
    """Send a local file back to the current conversation's chat."""

    name = "chat_send_file"
    needs_session = True
    description = (
        "Send a local file (audio, music, sfx, image, video, or any artifact) "
        "back to THIS conversation's chat as a real attachment. Use it to hand "
        "over something you just generated — e.g. after `navig audio gen "
        "\"…\" --kind music` (also sfx/tts), an image render, or any file the "
        "operator should receive. Chat routing is automatic from the session; "
        "pass the file's absolute path. Returns an honest delivery receipt."
    )
    owner_only = False
    parameters: list[dict[str, Any]] = [
        {
            "name": "path",
            "type": "string",
            "description": "Absolute path of the file to send.",
            "required": True,
        },
        {
            "name": "caption",
            "type": "string",
            "description": "Optional caption shown with the file.",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.platform.paths import resolve_user_path

        raw_path = str(args.get("path") or "").strip()
        if not raw_path:
            return ToolResult(
                name=self.name, success=False, output="path is required.", error="missing path"
            )

        try:
            path = resolve_user_path(raw_path)
        except Exception:  # noqa: BLE001 — an unresolvable path is the model's typo
            from pathlib import Path

            path = Path(raw_path)

        from pathlib import Path

        target = Path(path)
        if not target.is_file():
            return ToolResult(
                name=self.name,
                success=False,
                output=f"No such file: {target}",
                error="file not found",
            )
        size = target.stat().st_size
        if size > MAX_SEND_BYTES:
            return ToolResult(
                name=self.name,
                success=False,
                output=(
                    f"{target.name} is {size / (1024 * 1024):.1f} MB — Telegram's "
                    f"bot upload limit is {MAX_SEND_BYTES // (1024 * 1024)} MB. "
                    "Split or compress it first."
                ),
                error="too large for Telegram",
            )

        delivery = await deliver_to_session(
            str(args.get("_session_id") or ""),
            target,
            str(args.get("caption") or "").strip(),
        )
        if not delivery.ok:
            return ToolResult(
                name=self.name, success=False, output=delivery.detail, error=delivery.error
            )
        return ToolResult(
            name=self.name,
            success=True,
            output=(
                f"Sent {target.name} ({size // 1024} KB) to {delivery.channel} chat "
                f"{delivery.thread}"
                + (f" as message {delivery.message_id}" if delivery.message_id else "")
                + "."
            ),
        )


def register_chat_media_tools() -> None:
    """Register chat_send_file in the ``core`` toolset (offered every turn)."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(ChatSendFileTool(), toolset="core")
    logger.debug("Chat media tools registered: chat_send_file")
