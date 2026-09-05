"""The /extensions card — switch bot features on and off from inside Telegram.

The operator noticed the problem in Telegram ("the habit system writes me every
day"), so the fix lives in Telegram. One screen, no drill-down: the body carries
the reading, the keyboard carries the acting — the same shape as
``habit_actions.build_pause_menu``, and for the same reason. A switch you cannot
verify is worse than no switch.

State is the module registry's ``modules.overrides`` (see
``navig.gateway.channels.telegram_extensions``), so this card, ``navig telegram
extensions`` and the Deck all read and write one truth.

Callback data (Telegram caps it at 64 bytes):
    xt:r              render / refresh the list
    xt:t:<id>         toggle extension <id>, re-render in place
    xt:i              the "what each one does" index
    xt:i:<id>         one extension's detail card
    xt:x              close — clear the keyboard, leave a one-line receipt
"""

from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

CALLBACK_PREFIX = "xt:"

#: State markers. A word plus an unmistakable mark, never a bare colour — the row
#: has to be answerable at a glance without a legend.
MARK_ON = "🟢"
MARK_OFF = "⚪"
MARK_LOCKED = "🔒"

#: Buttons per row. Two fits a phone without truncating a label like
#: "Remote & Docker".
_COLUMNS = 2


def _rows_for(extensions: list[dict[str, Any]]) -> list[list[dict[str, str]]]:
    """Build the toggle keyboard.

    Order follows the body list exactly and is state-INDEPENDENT: sorting
    enabled-first would move a button out from under the operator's finger on the
    re-render that their own tap triggered.
    """
    rows: list[list[dict[str, str]]] = []
    row: list[dict[str, str]] = []
    for ext in extensions:
        mark = MARK_LOCKED if ext.get("locked") else (
            MARK_ON if ext.get("enabled") else MARK_OFF
        )
        row.append({
            "text": f"{mark} {ext['label']}",
            "callback_data": f"{CALLBACK_PREFIX}t:{ext['key']}",
        })
        if len(row) == _COLUMNS:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        {"text": "ℹ️ What each one does", "callback_data": f"{CALLBACK_PREFIX}i"},
        {"text": "✕ Close", "callback_data": f"{CALLBACK_PREFIX}x"},
    ])
    return rows


def build_list() -> tuple[str, dict[str, Any]]:
    """The extensions screen: (HTML text, inline keyboard)."""
    from navig.gateway.channels.telegram_extensions import list_extensions

    payload = list_extensions()
    exts = payload["extensions"]
    counts = payload["counts"]

    lines = [
        f"🧩 <b>Extensions</b> — {counts['on']} of {counts['total']} on",
        "",
    ]
    for group in payload["group_order"]:
        in_group = [e for e in exts if e["group"] == group]
        if not in_group:
            continue
        lines.append(f"<b>{html.escape(group)}</b>")
        for ext in in_group:
            if ext.get("locked"):
                mark, tail = MARK_LOCKED, f" — {ext.get('min_tier') or 'locked'}"
            elif ext["enabled"]:
                mark, tail = MARK_ON, f" — {html.escape(ext['description'])}"
            else:
                mark, tail = MARK_OFF, f" — {html.escape(ext['description'])}"
            lines.append(f"{mark} <b>{html.escape(ext['label'])}</b>{tail}")
        lines.append("")

    lines += [
        "<i>Off means gone: the commands leave /help and the \"/\" menu, the "
        "buttons stop answering, and nothing it sends arrives. Nothing is "
        "deleted — switching it back on restores it exactly as it was.</i>",
        "",
        "<i>Tap one to flip it.</i>",
    ]
    return "\n".join(lines), {"inline_keyboard": _rows_for(exts)}


def build_detail(key: str | None = None) -> tuple[str, dict[str, Any]]:
    """Detail for one extension, or the index when *key* is None."""
    from navig.gateway.channels.telegram_extensions import all_extensions, get, is_enabled

    back = [[{"text": "◀ Extensions", "callback_data": f"{CALLBACK_PREFIX}r"}]]

    if not key:
        lines = ["🧩 <b>What each one does</b>", ""]
        for ext in all_extensions():
            lines.append(
                f"<b>{html.escape(ext.label)}</b> — {html.escape(ext.description)}"
            )
        return "\n".join(lines), {"inline_keyboard": back}

    ext = get(key)
    if ext is None:
        return "That extension no longer exists.", {"inline_keyboard": back}

    enabled = is_enabled(ext.id)
    lines = [
        f"🧩 <b>{html.escape(ext.label)}</b>",
        html.escape(ext.description),
        "",
        f"<b>State</b>  {MARK_ON + ' on' if enabled else MARK_OFF + ' off'}",
    ]
    if ext.commands:
        cmds = " ".join(f"/{c}" for c in sorted(ext.commands))
        lines.append(f"<b>Commands</b>  {html.escape(cmds)}")
    if ext.about:
        lines += ["", "<b>Switching it off</b>"]
        lines += [f"· {html.escape(line)}" for line in ext.about]

    rows = [[{
        "text": f"{MARK_OFF} Switch off" if enabled else f"{MARK_ON} Switch on",
        "callback_data": f"{CALLBACK_PREFIX}t:{ext.id}",
    }]] + back
    return "\n".join(lines), {"inline_keyboard": rows}


async def handle_callback(
    channel: Any,
    cb_data: str,
    chat_id: int,
    message_id: int,
    user_id: int | None = None,
) -> str:
    """Apply one tap and re-render in place. Returns the toast text.

    The toast is answered by the caller AFTER this returns, so it can report what
    actually happened rather than what was about to be attempted.
    """
    from navig.gateway.channels.telegram_extensions import get, is_enabled

    action = cb_data[len(CALLBACK_PREFIX):] if cb_data.startswith(CALLBACK_PREFIX) else ""

    if action in ("", "r"):
        text, keyboard = build_list()
        await _edit(channel, chat_id, message_id, text, keyboard)
        return ""

    if action == "x":
        from navig.gateway.channels.telegram_extensions import list_extensions

        counts = list_extensions()["counts"]
        await _edit(
            channel, chat_id, message_id,
            f"🧩 {counts['on']} of {counts['total']} extensions on. "
            "/extensions to open this again.",
            None,
        )
        return ""

    if action == "i" or action.startswith("i:"):
        text, keyboard = build_detail(action[2:] if action.startswith("i:") else None)
        await _edit(channel, chat_id, message_id, text, keyboard)
        return ""

    if action.startswith("t:"):
        key = action[2:]
        ext = get(key)
        if ext is None:
            return "That extension no longer exists"
        if ext.locked:
            # Should be unreachable — a locked extension is never rendered as a
            # button — but a stale card from an older build could still carry one.
            return f"{ext.label} cannot be switched off"

        # Re-read the lock at TAP time, not at render time: a keyboard built
        # before a licence change must not be able to toggle something now locked.
        toggled, toast = _apply_toggle(ext, currently=is_enabled(ext.id))
        text, keyboard = build_list()
        await _edit(channel, chat_id, message_id, text, keyboard)
        if toggled:
            await _refresh_bot_commands(channel)
        return toast

    return ""


def _apply_toggle(ext: Any, *, currently: bool) -> tuple[bool, str]:
    """Persist the flip. Returns (did_change, toast)."""
    from navig.modules.registry import get_registry

    target = not currently
    try:
        ok = get_registry().set_enabled(ext.module_id, target)
    except Exception as exc:  # noqa: BLE001
        logger.warning("extension toggle %s failed: %s", ext.module_id, exc)
        return False, "⚠️ Nothing changed"
    if not ok:
        return False, "⚠️ Nothing changed"

    n = len(ext.commands)
    if target:
        detail = f"{n} commands back" if n else "back on"
        return True, f"{MARK_ON} {ext.label} on — {detail}"
    detail = f"{n} commands hidden" if n else "switched off"
    return True, f"{MARK_OFF} {ext.label} off — {detail}"


async def _refresh_bot_commands(channel: Any) -> None:
    """Re-publish the "/" autocomplete so a switched-off command disappears.

    Best-effort: ``setMyCommands`` is rate-limited and client-cached, and the
    dispatch gate already makes a stale entry harmless. A failed refresh must
    never make a successful toggle look broken.
    """
    try:
        register = getattr(channel, "_register_commands", None)
        if register is not None:
            await register()
    except Exception as exc:  # noqa: BLE001
        logger.debug("command re-registration after extension toggle skipped: %s", exc)


async def _edit(
    channel: Any,
    chat_id: int,
    message_id: int,
    text: str,
    keyboard: dict[str, Any] | None,
) -> None:
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    # Explicitly CLEAR rather than omit: omitting reply_markup leaves the old
    # buttons live on a message that says the card is closed.
    payload["reply_markup"] = keyboard if keyboard is not None else {"inline_keyboard": []}
    try:
        await channel._api_call("editMessageText", payload)
    except Exception as exc:  # noqa: BLE001
        # "message is not modified" is Telegram's answer to a tap that changed
        # nothing visible; not a failure worth surfacing.
        logger.debug("extensions card edit failed: %s", exc)
