"""A week of entries, read together — the pattern-level half of "analyse my psychology".

The daily read-back sees one page. Only a weekly read can see what moved across
pages: what the writer kept returning to, what shifted, what was said once and
dropped. That is what this asks for, and the tests hold the same two properties
the daily one does — the journal is never at risk, and nothing reads it without
the operator's opt-in — plus one of its own: the reader must not hand the model
its own previous output as if the operator had written it.
"""

from __future__ import annotations

import json
import pathlib
from datetime import date

import pytest

from navig.spaces import journal, journal_reflection

LOCALES = pathlib.Path(__file__).resolve().parents[2] / "navig" / "locales"

SUN = date(2026, 9, 13)  # a Sunday
MON = date(2026, 9, 7)


@pytest.fixture
def tracker(tmp_path):
    return tmp_path / "habits.csv"


def _week(tracker, days=("2026-09-08", "2026-09-10", "2026-09-13")):
    for d in days:
        journal.append_entry(tracker, d, f"On {d} the client call ate the morning again.")


# ── the reader ───────────────────────────────────────────────────────────────


def test_the_review_written_into_a_day_is_not_read_as_that_days_entry(tracker):
    """⚠ The property this feature depends on most.

    `append_block` writes the weekly review into the same file as the day's
    entry, under `## Review`. A reader that took the whole file would feed the
    model its own previous reflection as though the operator had written it —
    and next week's reflection would quote it back as "their own words".
    """
    journal.append_entry(tracker, "2026-09-13", "Sunday. Tired but the week held.")
    journal.append_block(
        tracker, "2026-09-13", "## Review — 7–13 September\n\nMODEL OUTPUT MUST NOT LEAK\n"
    )

    bodies = journal.entry_bodies(tracker, "2026-09-13")

    assert bodies == ["Sunday. Tired but the week held."]
    assert all("MODEL OUTPUT" not in b for b in bodies)


def test_two_entries_on_one_day_read_as_one_piece(tracker):
    journal.append_entry(tracker, "2026-09-08", "Long morning.")
    journal.append_entry(tracker, "2026-09-08", "Forgot: called mum.")

    [(day, text)] = journal.entries_between(tracker, MON, SUN)

    assert day == "2026-09-08"
    assert "Long morning." in text and "called mum" in text


def test_paragraph_breaks_survive_the_reader(tracker):
    """The writer keeps them (that was the whole point of the storage change);
    the reader must not flatten them on the way back out."""
    journal.append_entry(tracker, "2026-09-08", "First thing.\n\nSecond thing, later.")

    [(_, text)] = journal.entries_between(tracker, MON, SUN)

    assert "First thing.\n\nSecond thing" in text


def test_empty_days_are_absent_not_padded(tracker):
    _week(tracker)

    days = [d for d, _ in journal.entries_between(tracker, MON, SUN)]

    assert days == ["2026-09-08", "2026-09-10", "2026-09-13"]


# ── gating ───────────────────────────────────────────────────────────────────


def test_one_entry_is_not_a_week(tracker):
    """Below two entries there is no pattern to read; the daily read-back already
    covered the one page there is."""
    journal.append_entry(tracker, "2026-09-13", "Only Sunday was written.")

    assert journal_reflection.week_text(tracker, SUN) is None


def test_the_week_text_heads_each_day_so_the_model_can_quote_across_days(tracker):
    _week(tracker)

    text = journal_reflection.week_text(tracker, SUN)

    assert text is not None
    for d in ("2026-09-08", "2026-09-10", "2026-09-13"):
        assert f"### {d}" in text


def test_only_sunday_closes_a_week():
    assert journal_reflection.closes_a_week("2026-09-13") is True  # Sunday
    assert journal_reflection.closes_a_week("2026-09-12") is False  # Saturday
    assert journal_reflection.closes_a_week("not-a-date") is False


@pytest.mark.asyncio
async def test_week_is_silent_when_the_operator_has_not_opted_in(tracker, monkeypatch):
    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: False)
    called: list[str] = []

    async def _spy(tool, content, **_k):
        called.append(tool)
        return {"ok": True, "result": "..."}

    from navig.telegram import ai_actions

    monkeypatch.setattr(ai_actions, "run_text_action", _spy)

    assert await journal_reflection.week(tracker, SUN) is None
    assert called == [], "the week was sent to the model without the opt-in"


@pytest.mark.asyncio
async def test_week_reads_the_assembled_entries_through_reflect_week(tracker, monkeypatch):
    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    seen: dict = {}

    async def _fake(tool, content, **kw):
        seen.update(tool=tool, content=content, owner=kw.get("is_owner"))
        return {"ok": True, "result": "  You kept coming back to the client call.  "}

    from navig.telegram import ai_actions

    monkeypatch.setattr(ai_actions, "run_text_action", _fake)

    body = await journal_reflection.week(tracker, SUN)

    assert body == "You kept coming back to the client call."
    assert seen["tool"] == "reflect_week"
    assert seen["owner"] is True
    assert "### 2026-09-08" in seen["content"] and "### 2026-09-13" in seen["content"]


@pytest.mark.asyncio
async def test_a_full_week_reaches_the_model_whole(tracker, monkeypatch):
    """`run_text_action` cut everything at 4,000 chars, silently, because it was
    sized for one Telegram message. Measured: a week of ~240-word entries is
    ~7,900 chars, and the cut kept Monday-Thursday and dropped Friday-Sunday —
    so the reflection was asked what shifted by Sunday having never seen it."""
    long_day = "Сегодня был длинный день, и я устал сильнее, чем думал. " * 30  # ~1.7k chars
    for d in range(7, 14):
        journal.append_entry(tracker, f"2026-09-{d:02d}", long_day)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    seen: dict = {}

    async def _fake(tool, content, **kw):
        seen["content"] = content
        return {"ok": True, "result": "ok"}

    from navig.telegram import ai_actions

    monkeypatch.setattr(ai_actions, "run_text_action", _fake)
    await journal_reflection.week(tracker, SUN)

    assert len(seen["content"]) > 4000, "fixture is not long enough to prove anything"
    assert "### 2026-09-13" in seen["content"], "Sunday was cut off the week"


@pytest.mark.asyncio
async def test_the_budget_is_actually_passed_through(tracker, monkeypatch):
    """The fake above replaces run_text_action entirely, so it cannot see the
    budget. This one keeps the real function up to the model call."""
    from navig.telegram import ai_actions

    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    captured: dict = {}

    def _fake_llm(messages, **kw):
        captured["user"] = messages[-1]["content"]
        return "ok"

    monkeypatch.setattr("navig.llm.generate.llm_generate", _fake_llm)
    big = "x" * 10_000
    res = await ai_actions.run_text_action(
        "reflect_week", big, is_owner=True, max_chars=journal_reflection.WEEK_MAX_CHARS
    )

    assert res["ok"]
    assert "cut here" not in captured["user"], "10k chars was cut under a 28k budget"

    # And the DEFAULT still protects the single-message callers: a cut is
    # marked in the text, never silent.
    res = await ai_actions.run_text_action("summarize", big, is_owner=True)
    assert res["ok"]
    assert "cut here" in captured["user"]
    assert len(captured["user"]) < 10_000


# ── which model reads it ─────────────────────────────────────────────────────


def test_journal_model_unset_and_empty_both_mean_no_pin(monkeypatch):
    """`navig config set journal.model ""` is how the pin is cleared (there is
    no `config unset`), so the empty string must read as unset."""
    from navig.config import get_config_manager

    for stored in (None, "", "   "):
        monkeypatch.setattr(
            type(get_config_manager()), "get", lambda self, k, d=None, _v=stored: _v, raising=False
        )
        assert journal_reflection.model_override() is None, f"{stored!r} read as a pin"


def test_journal_model_pins_the_reflection_and_nothing_else(tracker, monkeypatch):
    """The privacy knob reaches `llm_generate` as `model_override`, and it reaches
    it ONLY from the journal callers — the default `run_text_action` path stays
    on the router (`mode="chat"`, no override), so a pinned journal does not
    drag every other text action onto a local model."""
    from navig.config import get_config_manager
    from navig.telegram import ai_actions

    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    monkeypatch.setattr(
        type(get_config_manager()),
        "get",
        lambda self, k, d=None: "ollama:llama3" if k == "journal.model" else d,
        raising=False,
    )
    calls: list[dict] = []

    def _fake_llm(messages, **kw):
        calls.append(kw)
        return "reflection"

    monkeypatch.setattr("navig.llm.generate.llm_generate", _fake_llm)

    assert (await_(journal_reflection.week(tracker, SUN))) == "reflection"
    assert calls[-1]["model_override"] == "ollama:llama3"
    assert calls[-1]["mode"] == journal_reflection.DEFAULT_MODE

    # An ordinary text action from the same process: router, fast tier, no pin.
    await_(ai_actions.run_text_action("summarize", "some message text here", is_owner=True))
    assert calls[-1]["model_override"] is None
    assert calls[-1]["mode"] == "chat"


def test_the_default_tier_is_quality_not_chat(tracker, monkeypatch):
    """`mode="chat"` resolves to small_talk — the fast conversational tier, which
    for this operator is a mini model. A week of someone's writing gets the
    big_tasks tier unless they pin something else."""
    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    monkeypatch.setattr(journal_reflection, "model_override", lambda: None)
    calls: list[dict] = []

    def _fake_llm(messages, **kw):
        calls.append(kw)
        return "reflection"

    monkeypatch.setattr("navig.llm.generate.llm_generate", _fake_llm)
    await_(journal_reflection.week(tracker, SUN))

    assert calls[-1]["mode"] == "big"
    assert calls[-1]["model_override"] is None


def await_(coro):
    import asyncio

    return asyncio.run(coro)


def test_reflect_week_is_owner_only_and_forbids_diagnosis():
    from navig.telegram import permissions
    from navig.telegram.ai_actions import _SYSTEM

    assert permissions.can_use("reflect_week", is_owner=True) is True
    assert permissions.can_use("reflect_week", is_owner=False) is False
    p = _SYSTEM["reflect_week"].lower()
    assert "no diagnosis" in p and "clinical" in p and "same language" in p
    # A week is where a model is most tempted to narrate day by day.
    assert "day by day" in p or "one by one" in p


@pytest.mark.parametrize("loc", ("en", "ru", "fr"))
def test_every_locale_has_the_week_heading(loc):
    d = json.loads((LOCALES / f"{loc}.json").read_text(encoding="utf-8"))
    assert d.get("habit.journal.reflect.week_heading")


# ── the Telegram surface ─────────────────────────────────────────────────────


class _Chan:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, chat_id, text, **_):
        self.sent.append(text)
        return {"message_id": 1}


async def _hook(channel, entry, *, day, tracker):
    from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

    await TelegramCommandsMixin._maybe_reflect_on_journal(
        channel, 42, entry, day=day, tracker=tracker
    )


@pytest.mark.asyncio
async def test_on_sunday_the_daily_read_back_is_followed_by_the_weeks(tracker, monkeypatch):
    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    tools: list[str] = []

    async def _fake(tool, content, **_k):
        tools.append(tool)
        return {"ok": True, "result": f"<{tool}>"}

    from navig.telegram import ai_actions

    monkeypatch.setattr(ai_actions, "run_text_action", _fake)

    ch = _Chan()
    await _hook(
        ch,
        "Sunday. A long enough entry to be reflected on properly.",
        day="2026-09-13",
        tracker=tracker,
    )

    assert tools == ["reflect", "reflect_week"], "order matters: the day, then the week"
    assert len(ch.sent) == 2
    assert "<reflect>" in ch.sent[0] and "<reflect_week>" in ch.sent[1]


@pytest.mark.asyncio
async def test_on_a_weekday_only_the_daily_read_back_is_sent(tracker, monkeypatch):
    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)
    tools: list[str] = []

    async def _fake(tool, content, **_k):
        tools.append(tool)
        return {"ok": True, "result": f"<{tool}>"}

    from navig.telegram import ai_actions

    monkeypatch.setattr(ai_actions, "run_text_action", _fake)

    ch = _Chan()
    await _hook(
        ch,
        "Wednesday. A long enough entry to be reflected on properly.",
        day="2026-09-10",
        tracker=tracker,
    )

    assert tools == ["reflect"]
    assert len(ch.sent) == 1


@pytest.mark.asyncio
async def test_a_failing_weekly_read_back_costs_nothing_already_sent(tracker, monkeypatch):
    """The daily reflection is already on screen; the week failing after it must
    neither raise nor retract anything."""
    _week(tracker)
    monkeypatch.setattr(journal_reflection, "is_enabled", lambda: True)

    async def _fake(tool, content, **_k):
        if tool == "reflect_week":
            raise RuntimeError("model unavailable")
        return {"ok": True, "result": "<reflect>"}

    from navig.telegram import ai_actions

    monkeypatch.setattr(ai_actions, "run_text_action", _fake)

    ch = _Chan()
    await _hook(
        ch,
        "Sunday. A long enough entry to be reflected on properly.",
        day="2026-09-13",
        tracker=tracker,
    )  # must not raise

    assert len(ch.sent) == 1 and "<reflect>" in ch.sent[0]
