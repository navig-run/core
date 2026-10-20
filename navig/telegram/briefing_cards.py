"""The morning and evening briefing cards: what they say, and whether they go out.

Rendered here rather than inline in :class:`navig.gateway.notifications.TelegramNotifier`
for two reasons. Every word used to be English literals in that class — the
operator runs NAVIG in Russian and received "Friday Evening · Evening Shift … The
graveyard is quiet" — so the text now comes from the locale files (``briefing.*``).
And the decision to send at all was nowhere: the ``briefings`` Telegram extension
promised "the scheduled morning and evening cards are not sent" while the notifier
never asked it, so switching it off only stripped the cards' buttons.

Config (all optional; defaults reproduce the old schedule):

* ``briefings.morning.enabled`` / ``briefings.evening.enabled`` — one card off,
  the other on. The extension toggle switches both.
* ``briefings.morning.time`` / ``briefings.evening.time`` — ``"HH:MM"``, local.
* ``briefings.days`` — ``"weekdays"`` (default), ``"daily"`` or ``"0,1,2,3,4"``
  (Monday = 0).
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, time
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("morning", "evening")
DEFAULT_TIMES = {"morning": time(7, 0), "evening": time(18, 0)}
WEEKDAYS = (0, 1, 2, 3, 4)
ALL_DAYS = (0, 1, 2, 3, 4, 5, 6)

#: Scheduler task name for each card — the notifier's existing names, kept so the
#: stored "last run" dates still match after an upgrade.
TASK_NAMES = {"morning": "morning_briefing", "evening": "evening_summary"}


# ── Policy ───────────────────────────────────────────────────────────────────


def _cfg_get(key: str, default: Any = None) -> Any:
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        node: Any = cm.global_config or {}
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node
    except Exception:  # noqa: BLE001 — a config read never takes down the scheduler
        return default


def extension_on() -> bool:
    try:
        from navig.gateway.channels.telegram_extensions import is_enabled

        return is_enabled("briefings")
    except Exception:  # noqa: BLE001
        return True


def card_enabled(kind: str) -> bool:
    """Whether the *kind* card may be sent: the extension AND its own switch."""
    from navig.core.coerce import coerce_bool

    if not extension_on():
        return False
    return coerce_bool(_cfg_get(f"briefings.{kind}.enabled", True), default=True)


def parse_time(raw: Any, default: time | None) -> time | None:
    try:
        hh, mm = str(raw).strip().split(":", 1)
        return time(int(hh), int(mm))
    except (TypeError, ValueError):
        return default


def card_time(kind: str) -> time:
    default = DEFAULT_TIMES[kind]
    raw = _cfg_get(f"briefings.{kind}.time", None)
    if raw in (None, ""):
        return default
    parsed = parse_time(raw, None)
    if parsed is None:
        logger.warning("ignoring briefings.%s.time=%r (want HH:MM)", kind, raw)
        return default
    return parsed


def parse_days(raw: Any) -> tuple[int, ...]:
    if raw in (None, ""):
        return WEEKDAYS
    if isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        word = str(raw).strip().lower()
        if word in ("weekdays", "workdays"):
            return WEEKDAYS
        if word in ("daily", "every", "all", "everyday"):
            return ALL_DAYS
        items = word.split(",")
    out: set[int] = set()
    for item in items:
        try:
            v = int(str(item).strip())
        except (TypeError, ValueError):
            continue
        if 0 <= v <= 6:
            out.add(v)
    return tuple(sorted(out)) or WEEKDAYS


def card_days() -> tuple[int, ...]:
    return parse_days(_cfg_get("briefings.days", None))


def status() -> dict[str, Any]:
    days = card_days()
    return {
        "extension": extension_on(),
        "days": list(days),
        **{
            kind: {
                "enabled": card_enabled(kind),
                "time": card_time(kind).strftime("%H:%M"),
            }
            for kind in KINDS
        },
    }


# ── Text ─────────────────────────────────────────────────────────────────────


def _t(key: str, **fields: Any) -> str:
    from navig.core import i18n

    return i18n.t(key, **fields)


def _pool(key: str) -> list[str]:
    from navig.core import i18n

    try:
        return i18n.shared().pool(key)
    except Exception:  # noqa: BLE001
        return []


def _pick(key: str) -> str:
    from navig.core import i18n

    return i18n.pick(key)


def _date_label(now: datetime) -> str:
    """"Friday, 9 October" — in the operator's language."""
    weekdays = _pool("briefing.weekdays")
    day = weekdays[now.weekday()] if len(weekdays) == 7 else now.strftime("%A")
    month_key = f"month.{now.month}"
    month = _t(month_key)
    if month == month_key:
        month = now.strftime("%B")
    return f"{day}, {now.day} {month}"


def build_morning(now: datetime, anchor: str = "") -> tuple[str, list[list[dict]] | None]:
    """``(html, keyboard)`` for the morning card. *anchor* is yesterday's
    "tomorrow's top priority", logged through the evening card."""
    lines = [
        f"☀️ <b>{html.escape(_t('briefing.morning.title'))}</b>"
        f"  ·  <i>{html.escape(_date_label(now))}</i>",
        "",
    ]
    if anchor:
        lines += [
            f"📌 <b>{html.escape(_t('briefing.morning.anchor'))}</b>",
            f"<i>{html.escape(anchor)}</i>",
            "",
        ]
    lines.append(f"<b>{html.escape(_t('briefing.morning.first_moves'))}</b>")
    lines += [f"• {html.escape(m)}" for m in _pool("briefing.morning.moves")]
    lines += ["", f"<i>{html.escape(_pick('briefing.morning.closings'))}</i>"]

    keyboard = None
    if anchor:
        keyboard = [[
            {"text": _t("briefing.button.anchor_keep"), "callback_data": "morn:anchor_ok"},
            {"text": _t("briefing.button.anchor_new"), "callback_data": "eve:plan_tomorrow"},
        ]]
    return "\n".join(lines), keyboard


def build_evening(now: datetime, *, shipped: str = "",
                  priority: str = "") -> tuple[str, str, list[list[dict]]]:
    """``(title, html, keyboard)`` for the evening card.

    *shipped* / *priority* are what was already logged today, so the card shows
    them as done and drops their buttons instead of asking twice."""
    contexts = _pool("briefing.evening.context")
    context = contexts[now.weekday()] if len(contexts) == 7 else ""
    title = _t("briefing.evening.title", date=_date_label(now))

    checklist = [f"<b>{html.escape(_t('briefing.evening.checklist'))}</b>"]
    if shipped:
        checklist.append(f"✅ <i>{html.escape(_t('briefing.evening.shipped', value=shipped))}</i>")
    else:
        checklist.append(f"• {html.escape(_t('briefing.evening.review'))}")
    if priority:
        checklist.append(f"📌 <i>{html.escape(_t('briefing.evening.anchor', value=priority))}</i>")
    else:
        checklist.append(f"• {html.escape(_t('briefing.evening.plan'))}")
    checklist += [
        f"• {html.escape(_t('briefing.evening.backups'))}",
        f"• {html.escape(_t('briefing.evening.close'))}",
    ]

    body = [f"🌙 <b>{html.escape(title)}</b>", ""]
    if context:
        body += [f"<i>{html.escape(context)}</i>", ""]
    body += [*checklist, "", f"<i>{html.escape(_pick('briefing.evening.closings'))}</i>"]

    row1 = []
    if not shipped:
        row1.append({"text": _t("briefing.button.log_shipped"), "callback_data": "eve:log_shipped"})
    if not priority:
        row1.append({"text": _t("briefing.button.plan_tomorrow"), "callback_data": "eve:plan_tomorrow"})
    row2 = [
        {"text": _t("briefing.button.backup_check"), "callback_data": "eve:backup_check"},
        {"text": _t("briefing.button.dnd_on"), "callback_data": "eve:dnd_on"},
    ]
    return title, "\n".join(body), [r for r in (row1, row2) if r]


def prompt_html(kind: str) -> str:
    """The follow-up a log/plan button posts. *kind*: ``shipped`` | ``priority``."""
    return (
        f"{'📦' if kind == 'shipped' else '🎯'} "
        f"<b>{html.escape(_t(f'briefing.prompt.{kind}.title'))}</b>\n\n"
        f"{html.escape(_t(f'briefing.prompt.{kind}.body'))}\n"
        f"<i>{html.escape(_t(f'briefing.prompt.{kind}.example'))}</i>\n\n"
        f"<i>{_t('briefing.prompt.cancel', word='</i><code>cancel</code><i>')}</i>"
    )


#: Replies that dismiss a pending log/plan prompt. Both languages: the operator
#: answers in whichever one their thumb picks.
CANCEL_WORDS = frozenset({"cancel", "skip", "no", "stop",
                          "отмена", "пропустить", "пропуск", "нет", "стоп"})
