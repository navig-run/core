"""
Telegram Native Checklists
============================
Automatically converts AI responses that consist predominantly of list items
into Telegram native checklist messages.  This turns task lists into tappable,
interactive checklists users can check off directly inside Telegram.

Detection logic:
- At least 3 list items
- At least 50 % of non-blank lines are list items
- Items are stripped of markdown bullets/numbers before sending

Fallback:
  When the native ``sendChecklist`` API call fails (bot token lacks permissions,
  older server version, etc.), the response is sent as an HTML-formatted message
  with ☐ / ☑ Unicode symbols and a note that the interactive checklist failed.

Integration:
  Replace ``self.send_message(...)`` calls in handlers with
  ``self._send_smart_reply(chat_id, text)`` to get automatic checklist
  detection and formatting.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants (single source of truth)
# ---------------------------------------------------------------------------
_CHECKLIST_MIN_ITEMS: int = 3          # minimum list items to trigger
_CHECKLIST_MIN_LIST_RATIO: float = 0.5 # fraction of non-blank lines that must be list items
_CHECKLIST_MAX_TITLE_LEN: int = 255    # Telegram's title field limit
_CHECKLIST_MAX_TASK_LEN: int = 100     # Telegram's per-task text limit

# Patterns that identify a line as a list item
_LIST_ITEM_RE = re.compile(
    r"""
    ^\s*                                  # leading whitespace
    (?:
        [-*+•]                            # unordered bullet
        | \d+[.)]\s                       # ordered: "1. " or "1) "
        | [a-zA-Z][.)]\s                  # lettered: "a. " or "a) "
        | (?:☐|☑|✅|❌|◻|▪)\s?           # checkbox/icon already present
    )
    \s*
    """,
    re.VERBOSE,
)

# Strip leading bullet/number from a line to get the task text
_STRIP_BULLET_RE = re.compile(
    r"""
    ^\s*
    (?:
        [-*+•]
        | \d+[.)]
        | [a-zA-Z][.)]
        | (?:☐|☑|✅|❌|◻|▪)\s?
    )
    \s*
    """,
    re.VERBOSE,
)


def extract_task_list(text: str) -> list[str] | None:
    """Parse *text* and return a list of task strings, or ``None`` if not a list.

    Returns ``None`` (not an empty list) when the text is not detected as a
    task list so callers can distinguish "no items" from "not a list".
    """
    if not text or not text.strip():
        return None

    lines = text.splitlines()
    non_blank = [ln for ln in lines if ln.strip()]
    if not non_blank:
        return None

    list_lines = [ln for ln in non_blank if _LIST_ITEM_RE.match(ln)]

    ratio = len(list_lines) / len(non_blank)
    if ratio < _CHECKLIST_MIN_LIST_RATIO or len(list_lines) < _CHECKLIST_MIN_ITEMS:
        return None

    tasks: list[str] = []
    for ln in list_lines:
        task = _STRIP_BULLET_RE.sub("", ln).strip()
        if task:
            tasks.append(task[:_CHECKLIST_MAX_TASK_LEN])

    return tasks if len(tasks) >= _CHECKLIST_MIN_ITEMS else None


def should_send_as_checklist(text: str) -> bool:
    """Return True if *text* qualifies for native checklist rendering."""
    return extract_task_list(text) is not None


def _derive_checklist_title(text: str, query: str) -> str:
    """Derive a short title for the checklist from the query or first line."""
    candidate = (query.strip() or text.strip().splitlines()[0]).strip()
    # Strip markdown symbols
    candidate = re.sub(r"[*_`#]+", "", candidate).strip()
    return candidate[:_CHECKLIST_MAX_TITLE_LEN] or "Tasks"


def _tasks_to_html_fallback(title: str, tasks: list[str]) -> str:
    """Format a task list as HTML checkboxes (fallback when native API fails)."""
    lines = [f"<b>{title}</b>"]
    for task in tasks:
        lines.append(f"☐ {task}")
    return "\n".join(lines)


class TelegramChecklistMixin:
    """Mixin — intercept AI list responses and send them as native checklists.

    Requires ``TelegramChannel`` to provide:
    - ``self._api_call(method, data)``
    - ``self.send_message(chat_id, text, …)``
    - ``self._bot_token`` or ``self._token`` (used for thread_id passthrough)
    """

    async def _send_smart_reply(
        self,
        chat_id: int,
        text: str,
        *,
        original_query: str = "",
        reply_to_message_id: int | None = None,
        thread_id: int | None = None,
        keyboard: list | None = None,
        parse_mode: str | None = "HTML",
    ) -> dict | None:
        """Send *text*, auto-upgrading to a native checklist when appropriate.

        Returns the result dict from the sent message (checklist or text).
        Falls back silently to plain HTML send if checklist is disabled or
        the API call fails.
        """
        cfg = self._get_checklist_config()
        if cfg.get("checklist_enabled", True) and should_send_as_checklist(text):
            tasks = extract_task_list(text)
            if tasks:  # guard against race between the two checks
                title = _derive_checklist_title(text, original_query)
                result = await self._try_send_checklist(
                    chat_id,
                    title,
                    tasks,
                    reply_to=reply_to_message_id,
                    thread_id=thread_id,
                )
                if result is not None:
                    return result
                # Native checklist failed — fall through to HTML fallback
                text = _tasks_to_html_fallback(title, tasks)
                parse_mode = "HTML"

        return await self.send_message(
            chat_id,
            text,
            parse_mode=parse_mode,
            reply_to_message_id=reply_to_message_id,
            keyboard=keyboard,
        )

    async def _try_send_checklist(
        self,
        chat_id: int,
        title: str,
        tasks: list[str],
        *,
        reply_to: int | None = None,
        thread_id: int | None = None,
    ) -> dict | None:
        """Call ``sendChecklist`` API.  Returns the result dict or ``None`` on failure."""
        payload: dict = {
            "chat_id": chat_id,
            "title": title,
            "tasks": [{"text": t, "completed": False} for t in tasks],
        }
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        if thread_id:
            payload["message_thread_id"] = thread_id

        try:
            result = await self._api_call("sendChecklist", payload)
            return result
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "sendChecklist unavailable for chat=%s (len=%d): %s — using HTML fallback",
                chat_id,
                len(tasks),
                exc,
            )
            return None

    async def _edit_checklist_task(
        self,
        chat_id: int,
        message_id: int,
        task_index: int,
        completed: bool,
    ) -> bool:
        """Toggle the completed state of a single checklist task.

        Returns True on success.  Silently returns False on any API error.
        """
        try:
            result = await self._api_call(
                "editMessageChecklist",
                {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "task_index": task_index,
                    "completed": completed,
                },
            )
            return result is not None
        except Exception as exc:  # noqa: BLE001
            logger.debug("editMessageChecklist failed: %s", exc)
            return False

    def _get_checklist_config(self) -> dict:
        """Return checklist config (best-effort)."""
        try:
            from navig.config import get_config_manager

            cm = get_config_manager()
            tg = cm.get("telegram") or {}
            return {"checklist_enabled": tg.get("checklist_enabled", True)}
        except Exception:  # noqa: BLE001
            return {"checklist_enabled": True}
