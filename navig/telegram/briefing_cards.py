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

from navig.telegram.card_doc import (
    Bullets,
    Button,
    Card,
    Heading,
    Para,
    Raw,
    Tasks,
    esc,
)

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
    """ "Friday, 9 October" — in the operator's language."""
    weekdays = _pool("briefing.weekdays")
    day = weekdays[now.weekday()] if len(weekdays) == 7 else now.strftime("%A")
    month_key = f"month.{now.month}"
    month = _t(month_key)
    if month == month_key:
        month = now.strftime("%B")
    return f"{day}, {now.day} {month}"


def morning_card(now: datetime, anchor: str = "") -> Card:
    """The morning card as structure — one definition, two renders.

    ``build_morning`` projects it for ``sendMessage``, ``build_morning_rich`` for
    ``sendRichMessage``: a heading, a section, a bullet list and an italic closing
    line become a real card (headline, checkbox-style moves, footer) on a client
    that renders rich messages, and keep the old layout everywhere else.

    Buttons appear only when there is an anchor to act on: the morning card has no
    other action of its own, and inventing a callback that nothing dispatches is
    how a card gets a button that does nothing.
    """
    # The briefing IS the screen: it arrives alone and owns the first scroll, so it
    # earns the display size every other card is denied.
    card = Card(title=_t("briefing.morning.title"), glyph="☀️", meta=_date_label(now), big=True)
    if anchor:
        card.add(Para(Raw(f"📌 <b>{esc(_t('briefing.morning.anchor'))}</b>")))
        card.add(Para(anchor, style="italic"))
    card.add(Heading(_t("briefing.morning.first_moves"), level=3))
    card.add(Bullets(_pool("briefing.morning.moves")))
    card.footer = _pick("briefing.morning.closings")

    if anchor:
        card.buttons(
            Button(_t("briefing.button.anchor_keep"), data="morn:anchor_ok", style="success"),
            Button(_t("briefing.button.anchor_new"), data="eve:plan_tomorrow", style="primary"),
        )
    return card


def build_morning(now: datetime, anchor: str = "") -> tuple[str, list[list[dict]] | None]:
    """``(html, keyboard)`` for the morning card. *anchor* is yesterday's
    "tomorrow's top priority", logged through the evening card."""
    card = morning_card(now, anchor)
    return card.classic_html(), card.keyboard()


def build_morning_rich(now: datetime, anchor: str = "") -> tuple[str, list[list[dict]] | None]:
    """``(rich_html, keyboard)`` — the same card for ``sendRichMessage``."""
    card = morning_card(now, anchor)
    return card.rich_html(), card.keyboard()


def brief_plain(now: datetime, *, anchor: str = "") -> str:
    """Today's brief as plain text — what a greeting card's **Open** reveals.

    The same content as :func:`build_morning`, without the HTML: card bodies are
    sent and edited without ``parse_mode``, so tags would read literally. An
    empty return means there is no brief to show; the caller falls back to the
    greeting's own text.
    """
    lines = [f"☀️ {_t('briefing.morning.title')}  ·  {_date_label(now)}", ""]
    if anchor:
        lines += [f"📌 {_t('briefing.morning.anchor')}", anchor, ""]
    moves = _pool("briefing.morning.moves")
    if not moves:
        return ""
    lines.append(_t("briefing.morning.first_moves"))
    lines += [f"• {move}" for move in moves]
    lines += ["", _pick("briefing.morning.closings")]
    return "\n".join(lines)


def evening_card(now: datetime, *, shipped: str = "", priority: str = "") -> Card:
    """The evening card as structure — see :func:`morning_card` for the contract.

    *shipped* / *priority* are what was already logged today, so the card shows
    them as done (a checked row, no button) instead of asking twice.
    """
    contexts = _pool("briefing.evening.context")
    context = contexts[now.weekday()] if len(contexts) == 7 else ""
    card = Card(
        title=_t("briefing.evening.title", date=_date_label(now)),
        glyph="🌙",
        meta=context,
        footer=_pick("briefing.evening.closings"),
        big=True,
    )
    card.add(Heading(_t("briefing.evening.checklist"), level=3))
    rows: list[tuple[str | Raw, bool]] = []
    if shipped:
        rows.append((_t("briefing.evening.shipped", value=shipped), True))
    else:
        rows.append((_t("briefing.evening.review"), False))
    if priority:
        rows.append((_t("briefing.evening.anchor", value=priority), True))
    else:
        rows.append((_t("briefing.evening.plan"), False))
    rows.append((_t("briefing.evening.backups"), False))
    rows.append((_t("briefing.evening.close"), False))
    card.add(Tasks(rows))

    if not shipped:
        card.buttons(
            Button(_t("briefing.button.log_shipped"), data="eve:log_shipped", style="success")
        )
    if not priority:
        card.buttons(
            Button(_t("briefing.button.plan_tomorrow"), data="eve:plan_tomorrow", style="primary")
        )
    card.buttons(
        Button(_t("briefing.button.backup_check"), data="eve:backup_check"),
        Button(_t("briefing.button.dnd_on"), data="eve:dnd_on"),
    )
    return card


def build_evening(
    now: datetime, *, shipped: str = "", priority: str = ""
) -> tuple[str, str, list[list[dict]]]:
    """``(title, html, keyboard)`` for the evening card."""
    card = evening_card(now, shipped=shipped, priority=priority)
    return card.title, card.classic_html(), card.keyboard()


def build_evening_rich(
    now: datetime, *, shipped: str = "", priority: str = ""
) -> tuple[str, str, list[list[dict]]]:
    """``(title, rich_html, keyboard)`` — the same card for ``sendRichMessage``."""
    card = evening_card(now, shipped=shipped, priority=priority)
    return card.title, card.rich_html(), card.keyboard()


# ── The screens behind the two evening buttons ───────────────────────────────
#
# "✅ What got done" used to answer with a sentence asking the operator to type what
# he shipped — at 18:00, when the day is a blur and the phone keyboard is the worst
# place to reconstruct it. The button now opens a picker: the last things he actually
# logged, one tap each, plus the free-text door. It edits the evening card in place,
# so the chat keeps one card instead of growing scaffolding underneath it.

#: How many past items a picker offers, and how far back it looks. Four fits one
#: thumb-scroll; a longer list is a log, not a choice.
PICK_LIMIT = 4
PICK_DAYS = 14

#: Kinds a picker can serve — the same two the evening card asks about.
PICK_KINDS = ("shipped", "priority")

#: The separator ``save_shipped`` writes between items of one day.
_ITEM_SEP = " · "


def _one_line(text: Any, limit: int) -> str:
    """A candidate as a button label: one line, no runaway width."""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    cut = flat[: limit - 1].rstrip(" ,;:.·-")
    return f"{cut}…"


def _recent(kind: str, limit: int, days: int) -> list[str]:
    from navig.agent.proactive import eve_log

    out: list[str] = []
    seen: set[str] = set()
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        entries = eve_log.history(days)
    except Exception:  # noqa: BLE001 — a picker with no history is still a picker
        logger.debug("briefing picker: no history", exc_info=True)
        return out
    for key, entry in entries.items():
        if key == today:
            # Today's own line is already the ticked row on the evening card; offering
            # it back would invite logging the same sentence twice.
            continue
        raw = str(entry.get(kind) or "")
        parts = raw.split(_ITEM_SEP) if kind == "shipped" else [raw]
        for part in parts:
            text = " ".join(part.split()).strip(" ,;.")
            if len(text) < 3:
                continue
            low = text.lower()
            if low in seen:
                continue
            seen.add(low)
            out.append(text)
            if len(out) >= limit:
                return out
    return out


def recent_shipped(limit: int = PICK_LIMIT, *, days: int = PICK_DAYS) -> list[str]:
    """What shipped on previous days, newest first, one item per choice."""
    return _recent("shipped", limit, days)


def recent_priorities(limit: int = PICK_LIMIT, *, days: int = PICK_DAYS) -> list[str]:
    """Tomorrow's-anchor candidates: the plans he already carried, newest first."""
    return _recent("priority", limit, days)


def pick_card(now: datetime, kind: str, candidates: list[str]) -> Card:
    """The screen behind “What got done” / “Plan tomorrow”.

    Every choice is a button that logs exactly the words it shows, and the two ways
    out (type something new, cancel) live in the last row — a picker with no exit is
    a dead end, and a cancel that posts a message instead of restoring the card
    leaves the chat holding a question nobody asked any more.
    """
    if kind not in PICK_KINDS:
        raise ValueError(f"unknown picker kind {kind!r}; want one of {PICK_KINDS}")
    shipped = kind == "shipped"
    card = Card(
        title=_t(f"briefing.pick.{kind}.title"),
        glyph="📦" if shipped else "🎯",
        meta=_date_label(now),
        footer=_t("briefing.pick.cancel_hint"),
    )
    card.add(Para(_t(f"briefing.pick.{kind}.body"), style="italic"))
    if candidates:
        card.add(Heading(_t(f"briefing.pick.{kind}.recent"), level=3))
        card.add(Para(_t("briefing.pick.tap_hint"), style="italic"))
        for index, text in enumerate(candidates):
            card.buttons(
                Button(
                    f"{index + 1} · {_one_line(text, 34)}",
                    data=f"eve:set:{kind}:{index}",
                    style="primary",
                )
            )
    else:
        card.add(Para(_t("briefing.pick.empty"), style="italic"))
    card.buttons(
        Button(_t("briefing.button.type_it"), data=f"eve:type:{kind}"),
        Button(_t("briefing.button.cancel"), data="eve:cancel"),
    )
    return card


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
CANCEL_WORDS = frozenset(
    {"cancel", "skip", "no", "stop", "отмена", "пропустить", "пропуск", "нет", "стоп"}
)
