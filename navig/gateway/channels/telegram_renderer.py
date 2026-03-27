"""
StatusRenderer — Live Telegram message pipeline renderer.

Edits a single Telegram message in-place as pipeline steps progress,
building a cinematic progress view with accumulated step history.

Usage:
    sentinel_msg = await channel.send_message(chat_id, "⬛⬛⬛⬛⬛⬛⬛⬛⬛⬛ Initializing...")
    renderer = StatusRenderer(channel, chat_id, sentinel_msg["message_id"])
    await renderer.update("Connecting...", progress=1)
    await renderer.update("Fetching data...", detail="3 sources queried", progress=4)
    await renderer.finalize(conclusion_dict, title="DIAGNOSIS", model_name="gpt-4o")
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.gateway.channels.telegram import TelegramChannel

logger = logging.getLogger(__name__)

_FILLED = "🟩"
_EMPTY = "⬛"
_BAR_WIDTH = 10
_MIN_EDIT_INTERVAL = 0.35  # seconds — stay under Telegram rate limit
_MAX_MESSAGE_LEN = 3_800  # safe under Telegram's 4096 hard limit


@dataclass
class _Step:
    icon: str
    text: str
    detail: str = ""
    is_warning: bool = False
    is_done: bool = False


class StatusRenderer:
    """
    Renders a live cinematic pipeline status view inside a single Telegram message.

    Thread note: designed for use within a single asyncio task.
    """

    def __init__(
        self,
        channel: TelegramChannel,
        chat_id: int,
        message_id: int,
    ) -> None:
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._steps: list[_Step] = []
        self._current_progress: int = 0
        self._last_edit_ts: float = 0.0
        self._start_ts: float = time.monotonic()

    # ── Public API ─────────────────────────────────────────────────

    async def update(
        self,
        step: str,
        detail: str = "",
        progress: int = 0,
        icon: str = "⚙️",
        warning: bool = False,
    ) -> None:
        """Add a step to the pipeline and edit the message in-place."""
        self._current_progress = max(self._current_progress, progress)
        self._steps.append(
            _Step(icon=icon, text=step, detail=detail, is_warning=warning)
        )
        await self._push()

    async def warn(self, tool_name: str, reason: str) -> None:
        """Record a skipped / failed tool inline."""
        self._steps.append(
            _Step(icon="⚠️", text=f"{tool_name} skipped", detail=reason, is_warning=True)
        )
        await self._push()

    async def finalize(
        self,
        conclusion: dict[str, Any],
        title: str = "RESULT",
        n_tools: int = 0,
        model_name: str = "",
        keyboard: list[list[dict]] | None = None,
    ) -> bool:
        """Render the final complete message with the conclusion block."""
        # Mark all steps done
        for s in self._steps:
            s.is_done = True
        self._current_progress = _BAR_WIDTH

        elapsed = time.monotonic() - self._start_ts
        footer = f"⚡ {elapsed:.1f}s · {n_tools} tools"
        if model_name:
            footer += f" · {model_name}"

        conclusion_block = _format_conclusion(conclusion, title, footer)
        full_text = self._build_frame(final=True, conclusion_block=conclusion_block)

        success = await self._edit(full_text, keyboard=keyboard)
        if not success:
            # Fall back to a new message if the edit failed (e.g. string too large or deleted sentinel)
            res = await self._channel.send_message(
                self._chat_id, full_text, parse_mode=None, keyboard=keyboard
            )
            return res is not None
        return True

    # ── Internal helpers ───────────────────────────────────────────

    def _progress_bar(self) -> str:
        filled = min(_BAR_WIDTH, max(0, self._current_progress))
        return _FILLED * filled + _EMPTY * (_BAR_WIDTH - filled)

    def _build_frame(
        self,
        final: bool = False,
        conclusion_block: str = "",
    ) -> str:
        lines: list[str] = []

        if final and conclusion_block:
            lines.append(f"{_FILLED * _BAR_WIDTH} ✅ Complete\n")
        else:
            bar = self._progress_bar()
            current_step = self._steps[-1].text if self._steps else "Initializing..."
            lines.append(f"{bar} {current_step}")

        # Accumulated step history (skip last in active mode — shown in bar)
        display_steps = self._steps if final else self._steps[:-1]
        for s in display_steps:
            icon = s.icon if not s.is_warning else "⚠️"
            line = f"  {icon} {s.text}"
            if s.detail:
                line += f"\n    └─ {s.detail}"
            lines.append(line)

        if final and conclusion_block:
            lines.append("")
            lines.append(conclusion_block)

        text = "\n".join(lines)
        # Ensure we stay under Telegram's message size limit
        if len(text) > _MAX_MESSAGE_LEN:
            text = text[: _MAX_MESSAGE_LEN - 20] + "\n…(truncated)"
        return text

    async def _push(self) -> None:
        """Rate-limited push: skip if called too soon after last edit."""
        now = time.monotonic()
        gap = now - self._last_edit_ts
        if gap < _MIN_EDIT_INTERVAL:
            await asyncio.sleep(_MIN_EDIT_INTERVAL - gap)
        await self._edit(self._build_frame())

    async def _edit(self, text: str, keyboard: list[list[dict]] | None = None) -> bool:
        try:
            res = await self._channel.edit_message(
                self._chat_id,
                self._message_id,
                text,
                parse_mode=None,  # plain text — avoids Markdown parse errors mid-stream
                keyboard=keyboard,
            )
            self._last_edit_ts = time.monotonic()
            if res is None:
                logger.warning("StatusRenderer edit_message returned None")
                return False
            return True
        except Exception as exc:
            # "message is not modified" is a normal no-op — swallow silently
            msg = str(exc).lower()
            if "not modified" not in msg and "message_not_modified" not in msg:
                logger.debug("StatusRenderer edit failed: %s", exc)
            return False


def _format_conclusion(
    data: dict[str, Any],
    title: str,
    footer: str,
) -> str:
    """Render key-value conclusion block."""
    line = "━" * 26
    rows = "\n".join(
        f"{str(k).ljust(14)}: {v}" for k, v in data.items() if v is not None and v != ""
    )
    return f"📋 {title}\n{line}\n{rows}\n{line}\n{footer}"
