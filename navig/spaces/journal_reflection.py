"""Reading the journal back — the one place both surfaces call.

The evening card's read-back (``_maybe_reflect_on_journal``) and the CLI's
``navig habit review`` both want the same two things: is the operator's opt-in
on, and what does a week of entries say. Keeping that here means the toggle is
read in one place and the week is assembled in one place, so the two surfaces
cannot drift into answering differently.

⚠ Everything here is OPT-IN behind ``journal.reflect`` (default OFF) and is
best-effort: a reflection is a bonus on top of the journal, never something the
journal can be made to wait for or fail on.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from navig.spaces import journal

#: Fewer entries than this and there is no week to read — only a day or two,
#: which the daily read-back already covered. Saying something anyway is how
#: a reflection becomes noise.
MIN_ENTRIES_FOR_A_WEEK = 2

#: Content budgets for the two read-backs, in characters. `run_text_action`
#: defaults to 4,000 — right for one Telegram message, wrong for writing.
#: A generous day runs to ~600 words (~4k chars in Russian); a week of them
#: to ~25k. Both sit far inside any current model's context.
DAY_MAX_CHARS = 8_000
WEEK_MAX_CHARS = 28_000

#: Python weekday() of the day a week closes on. Sunday: the card asks for the
#: day in the evening, so the week's read-back follows Sunday's entry.
WEEK_CLOSES_ON = 6


def is_enabled() -> bool:
    """``journal.reflect``, read through ``coerce_bool``.

    ``navig config set journal.reflect false`` stores the STRING "false", which
    is truthy — the documented-toggle footgun this repo gates on. Never read the
    key any other way.
    """
    from navig.core.coerce import coerce_bool

    try:
        from navig.config import get_config_manager

        return coerce_bool(get_config_manager().get("journal.reflect", False), default=False)
    except Exception:  # noqa: BLE001
        return False


#: Router hint for both read-backs when `journal.model` is unset. "big" is the
#: quality tier (`big_tasks`); "chat" would be the fast conversational tier,
#: which is the right default for reacting to a message and the wrong one for
#: reading someone's week.
DEFAULT_MODE = "big"


def model_override() -> str | None:
    """``journal.model`` — a ``provider:model`` spec that pins the journal.

    The privacy knob. Both read-backs send private writing to a model, and
    which model is not something to decide for the operator: with this set
    (say ``ollama:llama3``) the journal — and only the journal — goes there,
    through ``llm_generate``'s existing ``model_override``, regardless of what
    every other mode is routed to. Unset means the ``DEFAULT_MODE`` tier.
    """
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get("journal.model", None)
    except Exception:  # noqa: BLE001
        return None
    spec = str(raw).strip() if raw is not None else ""
    return spec or None


def closes_a_week(day: str) -> bool:
    """Whether *day* (ISO) is the day a week's read-back should follow."""
    try:
        return date.fromisoformat(day).weekday() == WEEK_CLOSES_ON
    except ValueError:
        return False


def week_text(tracker: Path, end: date) -> str | None:
    """The seven days ending on *end*, assembled for reading — or ``None``.

    ``None`` means "not enough of a week to read", by ``MIN_ENTRIES_FOR_A_WEEK``.
    Each day is headed by its date so the reader can quote across days.
    """
    entries = journal.entries_between(tracker, end - timedelta(days=6), end)
    if len(entries) < MIN_ENTRIES_FOR_A_WEEK:
        return None
    return "\n\n".join(f"### {day}\n\n{text}" for day, text in entries)


async def week(tracker: Path, end: date) -> str | None:
    """A reflection over the week ending on *end*, or ``None`` when there is none.

    ``None`` covers every "nothing to say" case — the toggle is off, too few
    entries, the model declined or failed. The caller sends nothing on ``None``;
    a failure the operator should hear about is the caller's own decision,
    because only the caller knows whether they asked for this just now.
    """
    if not is_enabled():
        return None
    text = week_text(tracker, end)
    if text is None:
        return None
    from navig.telegram import ai_actions

    res = await ai_actions.run_text_action(
        "reflect_week",
        text,
        is_owner=True,
        max_chars=WEEK_MAX_CHARS,
        mode=DEFAULT_MODE,
        model_override=model_override(),
    )
    if not res.get("ok"):
        return None
    body = (res.get("result") or "").strip()
    return body or None
