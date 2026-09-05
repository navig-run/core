"""Tests for navig.telegram.body_actions — the weigh-in prompt and weekly card.

Guards the ways a check-in silently stops recording:
  - a prompt whose answer cannot be identified, so a typed weight is lost;
  - callback_data over Telegram's 64-byte cap, which makes a button inert;
  - a wrong-language card, because the language was read through the config view
    that drops the whole `user` subtree;
  - a treatment note overwriting the day's existing notes;
  - an unparseable answer clearing the prompt, so the retry lands nowhere.

Hermetic: NAVIG_CONFIG_DIR is redirected, so the cross-process pin never touches
the operator's real ~/.navig.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from navig.spaces import body_metrics as bm
from navig.telegram import body_actions as ba

HEADER = "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n"
LOCALES = Path(ba.__file__).parent / "body_locales"
DAY = "2026-09-06"
CHAT = 159901607


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Redirect the pin file away from the operator's real config dir."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    ba._store = None          # locale cache is process-global
    yield
    ba._store = None


@pytest.fixture
def metrics(tmp_path):
    path = tmp_path / "metrics.csv"
    path.write_text(HEADER, encoding="utf-8")
    return path


@pytest.fixture
def two_weeks(metrics):
    """Previous week averages 89.0, current 88.0 — a clean -1.0 kg trend."""
    end = date.fromisoformat(DAY)
    for i in range(7):
        bm.upsert(metrics, (end - timedelta(days=13 - i)).isoformat(), {"weight_kg": "89"})
    for i in range(7):
        bm.upsert(metrics, (end - timedelta(days=6 - i)).isoformat(), {"weight_kg": "88"})
    return metrics


def _speak(monkeypatch, lang: str) -> None:
    monkeypatch.setattr(ba, "_lang", lambda: lang)
    ba._store = None


# ── language ──────────────────────────────────────────────────────────────────


def test_every_locale_defines_the_same_keys():
    """A missing key degrades that ONE string to English — invisible in review."""
    sets = {
        p.name: set(json.loads(p.read_text(encoding="utf-8")))
        for p in LOCALES.glob("*.json")
    }
    assert len(sets) == 3
    reference = sets["en.json"]
    for name, keys in sets.items():
        assert keys == reference, f"{name} differs: {keys ^ reference}"


def test_no_locale_string_is_empty():
    for p in LOCALES.glob("*.json"):
        for key, value in json.loads(p.read_text(encoding="utf-8")).items():
            assert value.strip(), f"{p.name}:{key} is empty"


@pytest.mark.parametrize(
    ("lang", "needle"),
    [("en", "Weight this morning"), ("ru", "Вес сегодня утром"), ("fr", "Poids ce matin")],
)
def test_prompt_follows_the_global_language(monkeypatch, metrics, lang, needle):
    _speak(monkeypatch, lang)
    text, _ = ba.build_weigh_prompt(metrics, DAY)
    assert needle in text


def test_a_language_with_no_table_falls_back_to_english(monkeypatch, metrics):
    _speak(monkeypatch, "zh")
    text, _ = ba.build_weigh_prompt(metrics, DAY)
    assert "Weight this morning" in text


def test_units_are_localized_not_hardcoded(monkeypatch, two_weeks):
    """A Russian card that says "kg" is half-translated — the tell for a hardcode."""
    _speak(monkeypatch, "ru")
    text, keyboard = ba.build_weekly_card(two_weeks, DAY)
    assert "кг" in text
    assert " kg" not in text
    sleep_row = keyboard["inline_keyboard"][1]
    assert all("ч" in b["text"] for b in sleep_row)


def test_language_resolution_uses_the_accessor_that_can_see_user_language(monkeypatch):
    """`_load_global_config()` DROPS the whole `user` subtree (#1192).

    Reading the language through it would make every card English forever, and
    nothing would ever error. Pin the resolver this module actually calls.
    """
    seen: list[str] = []

    def fake_resolve(override=""):
        seen.append("resolve_language")
        return "Russian"

    monkeypatch.setattr("navig.core.language.resolve_language", fake_resolve)
    ba._store = None
    assert ba._lang() == "ru"
    assert seen == ["resolve_language"]


# ── the weigh-in prompt ───────────────────────────────────────────────────────


def test_prompt_uses_force_reply_so_the_answer_is_identifiable(metrics):
    _, markup = ba.build_weigh_prompt(metrics, DAY)
    assert markup == {"force_reply": True, "selective": True}


def test_prompt_carries_no_inline_keyboard(metrics):
    """Telegram will not deliver force_reply AND an inline keyboard together."""
    _, markup = ba.build_weigh_prompt(metrics, DAY)
    assert "inline_keyboard" not in markup


def test_prompt_shows_the_last_reading_when_there_is_one(metrics):
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88.2"})
    text, _ = ba.build_weigh_prompt(metrics, DAY)
    assert "88.2" in text


def test_first_ever_prompt_says_so_instead_of_showing_a_blank(monkeypatch, metrics):
    _speak(monkeypatch, "en")
    text, _ = ba.build_weigh_prompt(metrics, DAY)
    assert "record starts here" in text


# ── the weekly card ───────────────────────────────────────────────────────────


def test_weekly_card_has_three_button_rows(two_weeks):
    _, keyboard = ba.build_weekly_card(two_weeks, DAY)
    assert len(keyboard["inline_keyboard"]) == 3


def test_every_callback_data_fits_telegrams_64_byte_cap(two_weeks):
    """Over the cap Telegram silently refuses the button — it just does nothing."""
    _, keyboard = ba.build_weekly_card(two_weeks, DAY)
    for row in keyboard["inline_keyboard"]:
        for button in row:
            assert len(button["callback_data"].encode()) <= 64


def test_every_callback_data_carries_the_module_prefix(two_weeks):
    _, keyboard = ba.build_weekly_card(two_weeks, DAY)
    for row in keyboard["inline_keyboard"]:
        for button in row:
            assert button["callback_data"].startswith(ba.CALLBACK_PREFIX)


def test_weekly_card_leads_with_the_average_not_the_latest(two_weeks):
    text, _ = ba.build_weekly_card(two_weeks, DAY)
    assert "88.0" in text          # the 7-day average
    assert "-1.0" in text          # the change against the previous week


def test_weekly_card_carries_the_safety_line(monkeypatch, two_weeks):
    _speak(monkeypatch, "en")
    text, _ = ba.build_weekly_card(two_weeks, DAY)
    assert "the doctor, not a workaround" in text


def test_weekly_card_survives_an_empty_record(metrics):
    text, keyboard = ba.build_weekly_card(metrics, DAY)
    assert text
    assert len(keyboard["inline_keyboard"]) == 3


def test_fast_loss_adds_a_neutral_line(monkeypatch, metrics):
    _speak(monkeypatch, "en")
    end = date.fromisoformat(DAY)
    for i in range(7):
        bm.upsert(metrics, (end - timedelta(days=13 - i)).isoformat(), {"weight_kg": "92"})
    for i in range(7):
        bm.upsert(metrics, (end - timedelta(days=6 - i)).isoformat(), {"weight_kg": "88"})
    text, _ = ba.build_weekly_card(metrics, DAY)
    assert "next appointment" in text


def test_ordinary_loss_does_not_add_it(monkeypatch, two_weeks):
    _speak(monkeypatch, "en")
    text, _ = ba.build_weekly_card(two_weeks, DAY)
    assert "next appointment" not in text


# ── callbacks ─────────────────────────────────────────────────────────────────


class _Channel:
    """Records the API calls a handler makes."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def _api_call(self, method, payload):
        self.calls.append((method, payload))
        return {"result": {"message_id": 4242}}


async def _tap(metrics, cb_data):
    bm.remember_target(CHAT, metrics, message_id=1, day=DAY)
    channel = _Channel()
    toast = await ba.handle_callback(channel, cb_data, CHAT, 1, CHAT)
    return toast, channel


async def test_mood_tap_is_written(metrics):
    toast, _ = await _tap(metrics, f"bm:m:8:{DAY.replace('-', '')}")
    assert toast
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["mood_1_10"] == "8"


async def test_sleep_tap_is_written(metrics):
    await _tap(metrics, f"bm:sl:7:{DAY.replace('-', '')}")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["sleep_hours"] == "7"


async def test_treatment_ok_is_recorded_as_a_note(metrics):
    await _tap(metrics, f"bm:tx:ok:{DAY.replace('-', '')}")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert "treatment: ok" in row["notes"]


async def test_treatment_note_asks_and_records_the_pending_prompt(metrics):
    _, channel = await _tap(metrics, f"bm:tx:no:{DAY.replace('-', '')}")
    assert channel.calls and channel.calls[0][0] == "sendMessage"
    assert channel.calls[0][1]["reply_markup"]["force_reply"] is True
    assert bm.pending_prompt(CHAT) == ("note", DAY, 4242)


async def test_a_note_does_not_discard_the_days_existing_notes(metrics):
    bm.upsert(metrics, DAY, {"notes": "slept badly"})
    await _tap(metrics, f"bm:tx:ok:{DAY.replace('-', '')}")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert "slept badly" in row["notes"]
    assert "treatment: ok" in row["notes"]


async def test_a_malformed_callback_does_not_raise(metrics):
    toast, _ = await _tap(metrics, "bm:")
    assert toast == "?"


# ── typed replies ─────────────────────────────────────────────────────────────


def _arm(metrics, kind="weigh"):
    bm.remember_target(CHAT, metrics, message_id=1, day=DAY)
    bm.set_prompt(CHAT, kind, DAY, 99)


def test_typed_weight_is_recorded(metrics):
    _arm(metrics)
    assert ba.consume_reply(CHAT, "87.4")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["weight_kg"] == "87.4"


def test_typed_weight_accepts_a_decimal_comma(metrics):
    _arm(metrics)
    ba.consume_reply(CHAT, "87,4")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["weight_kg"] == "87.4"


def test_answering_clears_the_prompt(metrics):
    _arm(metrics)
    ba.consume_reply(CHAT, "87.4")
    assert bm.pending_prompt(CHAT) is None


def test_with_no_prompt_outstanding_a_number_is_not_consumed(metrics):
    bm.remember_target(CHAT, metrics)
    assert ba.consume_reply(CHAT, "87.4") is None


def test_an_unreadable_answer_keeps_the_question_open(monkeypatch, metrics):
    """Clearing the prompt here would send the retry into the void."""
    _speak(monkeypatch, "en")
    _arm(metrics)
    assert "Could not read" in ba.consume_reply(CHAT, "about the same")
    assert bm.pending_prompt(CHAT) is not None
    ba.consume_reply(CHAT, "87.4")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["weight_kg"] == "87.4"


def test_skip_records_nothing_and_closes_the_question(metrics):
    _arm(metrics)
    ba.consume_reply(CHAT, "skip")
    assert bm.pending_prompt(CHAT) is None
    assert not [r for r in bm.read_metrics(metrics)[1] if r.get("weight_kg")]


def test_same_copies_the_last_reading_forward(metrics):
    bm.upsert(metrics, "2026-09-01", {"weight_kg": "88.8"})
    _arm(metrics)
    ba.consume_reply(CHAT, "same")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["weight_kg"] == "88.8"


def test_an_implausible_jump_needs_the_same_number_twice(metrics):
    """A typo is rarely made twice identically; a real 6 kg change is two sends."""
    bm.upsert(metrics, "2026-09-01", {"weight_kg": "88"})
    _arm(metrics)

    first = ba.consume_reply(CHAT, "188")
    assert not [r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY]
    assert first

    ba.consume_reply(CHAT, "188")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["weight_kg"] == "188"


def test_a_different_second_number_replaces_the_pending_one(metrics):
    bm.upsert(metrics, "2026-09-01", {"weight_kg": "88"})
    _arm(metrics)
    ba.consume_reply(CHAT, "188")
    ba.consume_reply(CHAT, "877")            # a different surprise — still not confirmed
    assert not [r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY]


def test_a_typed_treatment_note_is_recorded_verbatim(metrics):
    _arm(metrics, kind="note")
    ba.consume_reply(CHAT, "some nausea on day two, ate less")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert "some nausea on day two, ate less" in row["notes"]


# ── the cross-process pin ─────────────────────────────────────────────────────


def test_pin_round_trips_the_target_file(metrics):
    bm.remember_target(CHAT, metrics, message_id=7, day=DAY)
    assert bm.resolve_target(CHAT) == metrics
    assert bm.last_card(CHAT) == (7, DAY)


def test_pin_survives_being_read_by_a_second_process(metrics):
    """The gateway is a DIFFERENT process — the pin must be on disk, not memory."""
    bm.remember_target(CHAT, metrics, message_id=7, day=DAY)
    assert bm._targets_file().exists()
    reloaded = json.loads(bm._targets_file().read_text(encoding="utf-8"))
    assert reloaded[str(CHAT)]["path"] == str(metrics)


def test_an_unknown_chat_falls_back_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    assert bm.resolve_target(999999).name == "metrics.csv"


# ── layering ──────────────────────────────────────────────────────────────────


def test_body_actions_never_imports_a_cli_command_module():
    """This runs INSIDE the gateway. It must not reach into navig.commands.

    It briefly did — for a single threshold constant — which is how the rule
    gets broken: not by a big dependency, but by one value that seemed too small
    to move. The value now lives in body_metrics, where both surfaces read it and
    neither can drift.
    """
    import ast

    tree = ast.parse(Path(ba.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").startswith("navig.commands")
    ]
    assert not offenders, f"gateway module imports a CLI command module: {offenders}"


def test_body_actions_uses_no_private_helper_of_another_module():
    """`bm._read_state()` and friends were reached into directly; now there is an API."""
    import ast

    tree = ast.parse(Path(ba.__file__).read_text(encoding="utf-8"))
    offenders = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "bm"
        and node.attr.startswith("_")
    }
    assert not offenders, f"reaching into body_metrics privates: {sorted(offenders)}"


def test_both_surfaces_share_one_fast_loss_threshold():
    """A CLI and a card that disagree about when to mention the doctor is worse
    than either rule alone."""
    from navig.commands import body as cli

    assert cli._FAST_LOSS_KG_PER_WEEK is bm.FAST_LOSS_KG_PER_WEEK


# ── a stale prompt must never misattribute ────────────────────────────────────


def test_a_superseded_prompt_cannot_file_a_weight_against_the_wrong_day(metrics):
    """Answering yesterday's card tomorrow must not backdate the reading.

    The daily 08:10 job calls set_prompt every morning, which REPLACES the
    pending one. So a reply to an older card no longer matches the stored
    message id and is not consumed — it falls through to ordinary chat instead
    of being recorded against the day that card was sent.

    This is currently guaranteed by set_prompt overwriting rather than merging,
    which is easy to "improve" into a merge without noticing what it costs. The
    day a weight is attributed to is not a detail — it is what every average and
    trend downstream is computed from.
    """
    bm.remember_target(CHAT, metrics)
    bm.set_prompt(CHAT, "weigh", "2026-09-05", 100)   # Monday's card
    bm.set_prompt(CHAT, "weigh", "2026-09-06", 200)   # Tuesday's card supersedes it

    kind, day, prompt_id = bm.pending_prompt(CHAT)
    assert (kind, day, prompt_id) == ("weigh", "2026-09-06", 200)

    # The reply-id check in the channel handler is what rejects the stale card;
    # assert the id it would compare against no longer matches Monday's message.
    assert prompt_id != 100

    ba.consume_reply(CHAT, "87.4")
    dates = [r["date"] for r in bm.read_metrics(metrics)[1] if r.get("weight_kg")]
    assert dates == ["2026-09-06"], f"weight filed against {dates}, not the live day"
