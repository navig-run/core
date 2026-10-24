"""In-place cards for the time-based notices the operator actually receives.

The morning greeting, the daily tip and the host (Outpost/Lighthouse) health
alert used to be plain text: no way to expand them, no way to dismiss them, and
no way to tell two of them apart. They now go out as cards from
:mod:`navig.telegram.cards` — **Open/View** expands, **Back** returns, **Close**
collapses the message in place and clears its keyboard.

The card framework dispatches a press with only ``cd:<prefix>:<action>:<key>``,
so the expanded text is remembered beside the claim, in one small JSON file
under :func:`navig.platform.paths.data_dir`. That keeps a card usable after a
daemon restart and costs one message-sized write per notice.

Prefixes (lower-case, short, and distinct from ``mail:``/``dist:``)::

    ngr  morning greeting   Open brief | Close
    ntp  daily tip          Open | Close
    nop  host health        View | Close

``ngr``/``ntp`` are Core's own notices; ``nop`` is the host-health card. All are
ungated in the extension matrix because ``cd:`` itself is owned by the locked
Core extension — the content belongs to core, not to an optional plugin.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.core.json_io import JsonReadError, atomic_write_json, load_json_for_update
from navig.telegram.cards import (
    DETAIL,
    SUMMARY,
    Context,
    Screen,
    register_card_action,
    result_screen,
    send_card,
)

logger = logging.getLogger(__name__)

#: Card prefixes registered with the framework. Keep them unique across the tree.
PREFIX_GREETING = "ngr"
PREFIX_TIP = "ntp"
PREFIX_OUTPOST = "nop"

#: The daily tip's header glyph — the notification type it replaces is "reminder".
TIP_GLYPH = "⏰"
#: A host-health alert is an alert, so it keeps the alert glyph.
OUTPOST_GLYPH = "🚨"

#: At most one host-health card per host in this rolling window.
OUTPOST_WINDOW_HOURS = 6.0

#: Cards kept on disk (a card still open in the chat must stay expandable).
MAX_CARDS = 60


@dataclass(frozen=True)
class Notice:
    """One card's content: what the summary says and what Open reveals."""

    title: str
    body: str
    detail: str = ""
    open_label: str = "Open"


# ── the remembered payloads ─────────────────────────────────────────────────


def store_path() -> Path:
    """Resolved at CALL time so ``NAVIG_DATA_DIR`` isolation applies in tests."""
    from navig.platform import paths

    return paths.data_dir() / "telegram" / "notice_cards.json"


def _load(path: Path) -> dict[str, Any]:
    data = load_json_for_update(path, default={})
    return data if isinstance(data, dict) else {}


def remember(prefix: str, key: str, notice: Notice, *, now: datetime | None = None) -> None:
    """Save the content behind one card, so a later press can expand it."""
    path = store_path()
    stamp = (now or datetime.now()).isoformat()
    with _locked(path):
        try:
            data = _load(path)
        except JsonReadError as exc:
            # Losing the payload only makes the detail fall back to the summary;
            # never let bookkeeping cost the message itself.
            logger.warning("notice_cards: %s unreadable, not saving %s: %s", path, key, exc)
            return
        data[f"{prefix}:{key}"] = {
            "title": notice.title,
            "body": notice.body,
            "detail": notice.detail,
            "open_label": notice.open_label,
            "updated_at": stamp,
        }
        _prune(data, now or datetime.now())
        try:
            atomic_write_json(data, path, indent=1)
        except OSError as exc:
            logger.warning("notice_cards: could not save %s: %s", key, exc)


def recall(prefix: str, key: str) -> dict[str, Any] | None:
    """The saved content for one card, or None when it has been pruned."""
    try:
        data = _load(store_path())
    except JsonReadError:
        return None
    row = data.get(f"{prefix}:{key}")
    return row if isinstance(row, dict) else None


def _prune(data: dict[str, Any], now: datetime) -> None:
    if len(data) <= MAX_CARDS:
        return
    newest = sorted(
        data.items(),
        key=lambda item: str((item[1] or {}).get("updated_at") or ""),
        reverse=True,
    )
    data.clear()
    data.update(dict(newest[:MAX_CARDS]))


# ── building the three notices ──────────────────────────────────────────────


def greeting_card(
    title: str,
    greeting: str,
    *,
    anchor: str = "",
    now: datetime | None = None,
) -> tuple[Screen, Context]:
    """The morning greeting card. Open reveals today's brief (or the greeting)."""
    from navig.gateway.notifications import routine_emoji
    from navig.telegram.briefing_cards import brief_plain

    moment = now or datetime.now()
    day = moment.strftime("%Y-%m-%d")
    brief = ""
    try:
        brief = brief_plain(moment, anchor=anchor).strip()
    except Exception:  # noqa: BLE001 — a brief that cannot render must not lose the greeting
        logger.debug("notice_cards: brief_plain failed", exc_info=True)
    notice = Notice(
        title=f"{routine_emoji(moment.hour)} {title}",
        body=_plain(greeting),
        detail=brief or _plain(greeting),
        open_label="Open brief",
    )
    remember(PREFIX_GREETING, day, notice, now=moment)
    return (
        Screen(title=notice.title, body=notice.body, kind=SUMMARY, open_label="Open brief"),
        Context(PREFIX_GREETING, day),
    )


def tip_card(
    title: str,
    message: str,
    *,
    feature: str = "",
    now: datetime | None = None,
) -> tuple[Screen, Context]:
    """The daily tip card. Open shows the full tip and its example command."""
    moment = now or datetime.now()
    day = moment.strftime("%Y-%m-%d")
    body, command = _split_try(_plain(message))
    example = command or _example_for(feature)
    detail = body
    if example:
        detail = f"{body}\n\nTry:\n`{example}`" if body else f"Try:\n`{example}`"
    notice = Notice(title=f"{TIP_GLYPH} {title}", body=body, detail=detail)
    remember(PREFIX_TIP, day, notice, now=moment)
    return Screen(title=notice.title, body=notice.body, kind=SUMMARY), Context(PREFIX_TIP, day)


def outpost_card(
    host: str,
    *,
    title: str = "",
    checked_at: datetime | None = None,
    checks: list[str] | tuple[str, ...] = (),
    now: datetime | None = None,
) -> tuple[Screen, Context]:
    """The host-health card: View shows the host, check time and failing checks."""
    moment = now or datetime.now()
    checked = checked_at or moment
    host = str(host or "").strip() or "host"
    failures = [str(item).strip() for item in checks if str(item).strip()]
    headline = f"{OUTPOST_GLYPH} {title}" if title else f"{OUTPOST_GLYPH} {host} unhealthy"
    summary = f"{host} · checked {checked.strftime('%Y-%m-%d %H:%M')}"
    if failures:
        summary += f"\n{len(failures)} check(s) failing"
    detail_lines = [f"Host: {host}", f"Checked: {checked.strftime('%Y-%m-%d %H:%M')}"]
    if failures:
        detail_lines.append("")
        detail_lines.append("Failing checks:")
        detail_lines.extend(f"✗ {line}" for line in failures)
    else:
        detail_lines.append("No per-check detail was reported.")
    key = f"{_slug(host)}-{moment.strftime('%Y%m%dT%H%M')}"
    notice = Notice(
        title=headline,
        body=summary,
        detail="\n".join(detail_lines),
        open_label="View",
    )
    remember(PREFIX_OUTPOST, key, notice, now=moment)
    return (
        Screen(title=notice.title, body=notice.body, kind=SUMMARY, open_label="View"),
        Context(PREFIX_OUTPOST, key),
    )


async def report_outpost_unhealthy(
    host: str,
    *,
    title: str = "",
    checks: list[str] | tuple[str, ...] = (),
    checked_at: datetime | None = None,
    chat_id: int | str | None = None,
    channel: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send the host-health card at most once per :data:`OUTPOST_WINDOW_HOURS`.

    This is the sender for a host watcher (the Outpost/Lighthouse health check).
    The claim is made before the send, so a restart or a second watcher cannot
    repeat the card inside the window; a suppressed run returns
    ``{"sent": False, "suppressed": True}`` and logs at debug level.
    """
    from navig.telegram.daily_once import daily_once

    moment = now or datetime.now()
    target = chat_id
    if target is None:
        try:
            from navig.messaging.notify_operator import resolve_operator_chat_id

            target = resolve_operator_chat_id()
        except Exception:  # noqa: BLE001 — no operator chat configured is reported, not guessed
            target = None
    if target is None:
        return {"sent": False, "suppressed": False, "host": host, "error": "no chat id"}

    if not daily_once(f"outpost:{host}", moment, window_hours=OUTPOST_WINDOW_HOURS):
        logger.debug("outpost card for %s suppressed — already sent this window", host)
        return {"sent": False, "suppressed": True, "host": host}

    screen, context = outpost_card(
        host, title=title, checked_at=checked_at, checks=checks, now=moment
    )
    sent = await send_card(target, screen, context, channel=channel)
    return {
        "sent": bool(sent),
        "suppressed": False,
        "host": host,
        "message_id": (sent or {}).get("message_id"),
        "text": screen.text(),
    }


# ── handlers ────────────────────────────────────────────────────────────────


def _screen_for(action: Any, prefix: str, *, missing: str) -> Screen | None:
    """Expand / return / fall back for one press. None = leave the card alone."""
    payload = recall(prefix, action.key)
    if payload is None:
        return result_screen(missing, "The notice is no longer stored on this machine.")
    title = str(payload.get("title") or "")
    body = str(payload.get("body") or "")
    detail = str(payload.get("detail") or "") or body
    open_label = str(payload.get("open_label") or "Open")
    if action.action in ("open", "view"):
        return Screen(title=title, body=detail, kind=DETAIL)
    if action.action == "back":
        return Screen(title=title, body=body, kind=SUMMARY, open_label=open_label)
    return None


async def handle_greeting(action: Any) -> Screen | None:
    return _screen_for(action, PREFIX_GREETING, missing="This greeting has been tidied away")


async def handle_tip(action: Any) -> Screen | None:
    return _screen_for(action, PREFIX_TIP, missing="This tip has been tidied away")


async def handle_outpost(action: Any) -> Screen | None:
    return _screen_for(action, PREFIX_OUTPOST, missing="This alert is no longer available")


def register() -> None:
    """Register the three prefixes. Idempotent — re-registering replaces handlers."""
    register_card_action(PREFIX_GREETING, handle_greeting)
    register_card_action(PREFIX_TIP, handle_tip)
    register_card_action(PREFIX_OUTPOST, handle_outpost)


# ── text helpers ────────────────────────────────────────────────────────────


def _plain(text: str) -> str:
    """Card text is sent without ``parse_mode``; drop the markdown bold markers.

    Backticked commands are kept verbatim — they read as the code-style line the
    operator copies from, and the edit-in-place transport does not transform
    them either.
    """
    return str(text or "").replace("**", "").strip()


def _split_try(text: str) -> tuple[str, str]:
    """``message`` → (body, example command) with the ``Try:`` line pulled out."""
    lines = str(text or "").splitlines()
    command = ""
    kept: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("try:"):
            command = stripped[4:].strip().strip("`").strip()
            continue
        kept.append(line)
    return "\n".join(kept).strip(), command


def _example_for(feature: str) -> str:
    if not feature:
        return ""
    try:
        from navig.agent.proactive.capability_promo import FEATURE_REGISTRY

        for info in FEATURE_REGISTRY:
            if info.key == feature:
                return str(info.example_command or "").strip()
    except Exception:  # noqa: BLE001 — the tip still reads without its example
        logger.debug("notice_cards: feature lookup failed for %s", feature, exc_info=True)
    return ""


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    """A short, Telegram-safe key fragment (lower-case, no separators run)."""
    return _SLUG_RE.sub("-", str(value or "").strip().lower()).strip("-")[:24] or "host"


def _locked(path: Path):
    """Serialise the remember() read-modify-write across processes.

    Reuses :mod:`navig.telegram.daily_once`'s lock implementation rather than
    forking it; a lock that cannot be taken degrades to unlocked.
    """
    from navig.telegram.daily_once import _locked as locked

    return locked(path)


# Register on import: the handlers must exist in every process that may receive a
# press (the gateway), not only in the one that sent the card.
register()


__all__ = [
    "MAX_CARDS",
    "OUTPOST_GLYPH",
    "OUTPOST_WINDOW_HOURS",
    "PREFIX_GREETING",
    "PREFIX_OUTPOST",
    "PREFIX_TIP",
    "TIP_GLYPH",
    "Notice",
    "greeting_card",
    "handle_greeting",
    "handle_outpost",
    "handle_tip",
    "outpost_card",
    "recall",
    "register",
    "remember",
    "report_outpost_unhealthy",
    "store_path",
]
