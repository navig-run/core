"""The ``/health`` answer as a card: a sheet of numbers, the trend, and the actions.

The command used to build one HTML string with column-padded labels
(``"⚖️  Latest:      <b>87.4 kg</b>"``) — column alignment survives only in a
monospace client, so on a phone the numbers landed in a ragged block, the
sparkline floated under it, and the three actions sat in a keyboard detached from
the numbers they act on.

The same numbers as a card: a two-column sheet (the card kit's rule — a key/value
sheet reads without rules), the sparkline as preformatted text, a clinician
warning as a callout, and the actions as styled buttons inside the card.

Nothing here reads the store: the caller passes the summary it already builds, so
this module stays a pure projection and its outputs are testable without a body
record.
"""

from __future__ import annotations

from typing import Any

#: Fallbacks so a catalog without the new keys still renders readable English.
_FALLBACK = {
    "health.card.latest": "Latest",
    "health.card.average": "7-day average",
    "health.card.logged": "Logged",
    "health.card.habits": "Active habits",
    "health.card.no_record": "No weight recorded yet — send /weigh to start the record.",
    "health.card.unreadable": (
        "Could not read the body record. Unknown is not zero: nothing below is a measurement."
    ),
    "health.card.weigh": "⚖️ Weigh in",
    "health.card.weekly": "📊 Weekly",
    "health.card.habits_button": "💪 Habits",
    "health.card.hint": "/weigh — log now · /body — weekly card",
}


def _label(key: str, fallback: str = "") -> str:
    """A locale string, or the English fallback when the catalog has no entry."""
    default = fallback or _FALLBACK.get(key, key)
    try:
        from navig.core import i18n

        text = i18n.t(key)
    except Exception:  # noqa: BLE001 — a missing catalog must not cost the card
        return default
    return default if text in (key, "") else text


def _plain(text: str) -> str:
    """Strip the few HTML tags a banner may carry, so a meta line is not escaped twice."""
    out, depth = [], 0
    for char in text or "":
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out).strip()


def health_card(
    summary: dict[str, Any] | None,
    *,
    habits: int | None = None,
    banner: str = "",
    unit: str = "kg",
    no_prior: str = "no prior week",
    warning: str = "",
):
    """The body record as a card.

    *summary* is :func:`navig.spaces.body_metrics.summarize` — ``None`` means the
    record could not be read at all, which is drawn as a warning rather than as a
    zero, because a confident zero is a lie the operator would act on. *warning* is
    the clinician line for a rate of change worth naming; it reads as a callout, not
    as body text, because it is the one line that should stop a scroll.
    """
    from navig.telegram.card_doc import (
        Card,
        Code,
        Para,
        PullQuote,
        Raw,
        Table,
    )

    card = Card(title=_label("health.card.title", "Health"), glyph="🏥")
    if banner:
        card.meta = _plain(banner)

    if summary is None:
        card.add(PullQuote(_label("health.card.unreadable")))
        return _with_buttons(card, habits_known=False)

    latest = summary.get("latest")
    rows: list[list[str | Raw]] = []
    if latest is None:
        card.add(Para(_label("health.card.no_record"), style="italic"))
    else:
        rows.append(
            [
                _label("health.card.latest"),
                Raw(
                    f"<b>{latest:g} {unit}</b> "
                    f"<i>{_plain(str(summary.get('latest_date') or ''))}</i>"
                ),
            ]
        )
        average = summary.get("average")
        if average is not None:
            trend = summary.get("trend")
            change = f"{trend:+.1f} {unit}" if trend is not None else no_prior
            rows.append(
                [
                    _label("health.card.average"),
                    Raw(f"<b>{average:.1f} {unit}</b> ({_plain(change)})"),
                ]
            )
        rows.append(
            [
                _label("health.card.logged"),
                Raw(
                    f"<b>{int(summary.get('recorded') or 0)}/{int(summary.get('days') or 0)}</b> days"
                ),
            ]
        )
    # The habit count is worth showing even before a first weighing: it is the
    # streak the operator can act on today.
    if habits is not None:
        rows.append([_label("health.card.habits"), Raw(f"<b>{int(habits)}</b>")])
    if rows:
        card.add(Table(rows=rows, bordered=False))

    spark = str(summary.get("sparkline") or "").strip()
    if spark:
        # Preformatted on purpose: the sparkline is drawn with block characters and
        # only a monospace face keeps its columns aligned.
        card.add(Code(spark))

    if warning:
        card.add(PullQuote(_plain(warning)))

    return _with_buttons(card, habits_known=habits is not None)


def _with_buttons(card, *, habits_known: bool):
    """Attach the three actions the card exists to offer."""
    from navig.telegram.card_doc import Button

    card.buttons(
        Button(_label("health.card.weigh"), data="slash:weigh", style="primary"),
    )
    row = [Button(_label("health.card.weekly"), data="slash:body")]
    if habits_known:
        row.append(Button(_label("health.card.habits_button"), data="slash:habits"))
    card.buttons(*row)
    card.footer = _label("health.card.hint")
    return card


def build(
    summary: dict[str, Any] | None,
    *,
    habits: int | None = None,
    banner: str = "",
    unit: str = "kg",
    no_prior: str = "no prior week",
    warning: str = "",
) -> tuple[str, str, list[list[dict]] | None]:
    """``(classic_html, rich_html, keyboard)`` for the ``/health`` answer."""
    card = health_card(
        summary,
        habits=habits,
        banner=banner,
        unit=unit,
        no_prior=no_prior,
        warning=warning,
    )
    return card.classic_html(), card.rich_html(), card.keyboard()
