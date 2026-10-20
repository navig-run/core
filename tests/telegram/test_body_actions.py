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


# ── the weekly card shows what was chosen, and says when it is done ──────────
#
# Reported 2026-09-27 from the operator's phone: *"when I click here it doesn't
# mark, it just says saved, so I can't see what I selected — and when all is
# finished, it stays, no notification."* `handle_callback` wrote the tap to disk
# and returned a toast; it never touched the card (the `message_id` it was handed
# went unused), and nothing ever noticed that all three questions were answered.

COMPACT = DAY.replace("-", "")


def _edits(channel):
    return [p for m, p in channel.calls if m == "editMessageText"]


def _sends(channel):
    return [p for m, p in channel.calls if m == "sendMessage"]


def _labels(markup):
    return [b["text"] for row in markup["inline_keyboard"] for b in row]


async def _tap_on(metrics, cb_data, channel=None, message_id=77):
    bm.remember_target(CHAT, metrics, message_id=1, day=DAY)
    channel = channel or _Channel()
    toast = await ba.handle_callback(channel, cb_data, CHAT, message_id, CHAT)
    return toast, channel


async def test_a_mood_tap_redraws_the_card_with_the_choice_marked(metrics):
    """The report, at the layer it lives in: a tap must change the card."""
    _, channel = await _tap_on(metrics, f"bm:m:8:{COMPACT}")

    edits = _edits(channel)
    assert edits, "the tap was saved but the card was never redrawn — nothing to see"
    assert edits[0]["message_id"] == 77, "the redraw must target the card that was tapped"
    labels = _labels(edits[0]["reply_markup"])
    assert "✓ 🙂" in labels, f"the chosen face is not marked: {labels}"
    assert labels.count("✓ 🙂") == 1 and not any(
        lab.startswith("✓") for lab in labels if lab != "✓ 🙂"
    ), "exactly the chosen button is marked"
    assert "🙂" in edits[0]["text"], "the answer is also written next to its question"


async def test_a_sleep_mark_matches_however_the_number_was_stored(metrics):
    """The CSV holds strings — a value written as "7.0" is still the 7h button."""
    bm.upsert(metrics, DAY, {"sleep_hours": "7.0"})
    text, markup = ba.build_weekly_card(metrics, DAY, chat_id=CHAT)
    assert f"✓ 7{ba.t('unit.h')}" in _labels(markup)


async def test_tapping_the_same_answer_again_does_not_redraw(metrics):
    """An identical redraw is refused by Telegram ("message is not modified"),
    which would put a false failure in the log on every double-tap."""
    channel = _Channel()
    await _tap_on(metrics, f"bm:m:8:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:m:8:{COMPACT}", channel)
    assert len(_edits(channel)) == 1


async def test_all_fine_twice_records_it_once(metrics):
    """Each tap used to append another "treatment: ok" to the day's notes."""
    await _tap_on(metrics, f"bm:tx:ok:{COMPACT}")
    await _tap_on(metrics, f"bm:tx:ok:{COMPACT}")
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == DAY)
    assert row["notes"].count("treatment: ok") == 1, row["notes"]


async def test_answering_all_three_folds_the_card_and_notifies(metrics):
    """The second half of the report: a finished check-in has to SAY it is done.

    An edit raises no notification, so completion is a new message — and the card
    itself folds to one ✎ button so it stops looking like an open question.
    """
    channel = _Channel()
    await _tap_on(metrics, f"bm:m:8:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:sl:7:{COMPACT}", channel)
    assert not _sends(channel), "nothing to announce until all three are answered"

    await _tap_on(metrics, f"bm:tx:ok:{COMPACT}", channel)

    sends = _sends(channel)
    assert len(sends) == 1, f"expected one completion message, got {len(sends)}"
    body = sends[0]["text"]
    assert ba.t("week.done_title") in body
    assert "🙂" in body and f"7{ba.t('unit.h')}" in body and ba.t("week.tx_ok") in body, body

    folded = _edits(channel)[-1]["reply_markup"]
    assert _labels(folded) == [ba.t("btn.edit")], f"the finished card did not fold: {folded}"


async def test_the_completion_message_fires_once_per_week(metrics):
    """Reopening the card to change one answer re-completes it. That must not
    notify again — the operator already knows the week is done."""
    channel = _Channel()
    for cb in (f"bm:m:8:{COMPACT}", f"bm:sl:7:{COMPACT}", f"bm:tx:ok:{COMPACT}"):
        await _tap_on(metrics, cb, channel)
    await _tap_on(metrics, f"bm:ed:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:m:4:{COMPACT}", channel)

    assert len(_sends(channel)) == 1


async def test_edit_reopens_the_full_keyboard_with_the_answers_marked(metrics):
    channel = _Channel()
    for cb in (f"bm:m:8:{COMPACT}", f"bm:sl:7:{COMPACT}", f"bm:tx:ok:{COMPACT}"):
        await _tap_on(metrics, cb, channel)

    toast, _ = await _tap_on(metrics, f"bm:ed:{COMPACT}", channel)

    reopened = _labels(_edits(channel)[-1]["reply_markup"])
    assert "✓ 🙂" in reopened and f"✓ {ba.t('btn.fine')}" in reopened, reopened
    assert len(reopened) == 12, "all three rows are back"
    assert toast == "", "reopening is not a save — it should not claim one"


async def test_asking_for_a_note_marks_that_the_answer_is_on_its_way(metrics):
    _, channel = await _tap_on(metrics, f"bm:tx:no:{COMPACT}")

    assert channel.calls[0][0] == "sendMessage", "the note prompt still goes first"
    card = _edits(channel)[-1]
    assert f"✓ {ba.t('btn.note')}" in _labels(card["reply_markup"])
    assert ba.t("week.tx_pending") in card["text"]


def test_the_latest_treatment_answer_wins():
    """Notes accumulate; a later "all fine" supersedes an earlier note."""
    assert ba._treatment_of("treatment: nausea monday; treatment: ok") == "ok"
    assert ba._treatment_of("treatment: ok; treatment: tired") == "note"
    assert ba._treatment_of("slept badly") is None


async def test_a_typed_note_that_finishes_the_week_redraws_and_announces(metrics):
    """The third answer can arrive as a typed reply, not a tap."""
    channel = _Channel()
    await _tap_on(metrics, f"bm:m:8:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:sl:7:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:tx:no:{COMPACT}", channel)
    ba.consume_reply(CHAT, "less appetite this week")

    finished = await ba.after_note(channel, CHAT, DAY)

    assert finished and ba.t("week.done_title") in finished
    assert ba.t("week.tx_note") in finished
    assert _edits(channel)[-1]["message_id"] == 77, "the card that was tapped is redrawn"


async def test_a_note_answered_after_the_morning_weigh_in_redraws_the_card(metrics):
    """The daily weigh-in overwrites `last_card` every morning. A note typed the
    day after the card must redraw the CARD, not the weigh-in prompt."""
    channel = _Channel()
    await _tap_on(metrics, f"bm:tx:no:{COMPACT}", channel, message_id=77)
    bm.remember_target(CHAT, metrics, message_id=555, day="2026-09-07")  # next morning
    bm.set_prompt(CHAT, "note", DAY, 4242)
    ba.consume_reply(CHAT, "fine really")

    await ba.after_note(channel, CHAT, DAY)

    assert _edits(channel)[-1]["message_id"] == 77


def test_the_edit_button_fits_the_callback_budget():
    assert len(f"bm:ed:{COMPACT}".encode()) <= 64


async def test_the_gateway_wires_a_finishing_note_into_one_completion_receipt(metrics):
    """Drives the real reply handler, not `after_note` directly — the gateway
    has to CALL it, and must send the completion instead of a bare "saved"."""
    from navig.gateway.channels.telegram import TelegramChannel

    class _Both(_Channel):
        def __init__(self):
            super().__init__()
            self.sent: list[str] = []

        async def send_message(self, chat_id, text, **_):
            self.sent.append(text)
            return {"result": {"message_id": 9}}

    channel = _Both()
    await _tap_on(metrics, f"bm:m:8:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:sl:7:{COMPACT}", channel)
    await _tap_on(metrics, f"bm:tx:no:{COMPACT}", channel)
    prompt_id = bm.pending_prompt(CHAT)[2]

    handled = await TelegramChannel._handle_pending_body_input(
        channel, chat_id=CHAT, text="appetite a bit lower", reply_to_message_id=prompt_id
    )

    assert handled is True
    assert len(channel.sent) == 1, f"one receipt per answer, got {channel.sent}"
    assert ba.t("week.done_title") in channel.sent[0], channel.sent[0]
    assert _edits(channel)[-1]["message_id"] == 77, "and the card itself was redrawn"


async def test_a_card_answered_before_the_redraw_existed_folds_on_its_next_tap(metrics):
    """The operator's own card on the day this shipped: all three answers were
    already on disk, written by the old code, so the next tap changes nothing.
    It must still fold the card — not announce "done" under an open one."""
    bm.upsert(metrics, DAY, {"mood_1_10": "8", "sleep_hours": "7", "notes": "treatment: ok"})

    _, channel = await _tap_on(metrics, f"bm:m:8:{COMPACT}")

    assert len(_sends(channel)) == 1, "the completion is announced"
    edits = _edits(channel)
    assert edits, "the card was left open under a 'done' announcement"
    assert _labels(edits[-1]["reply_markup"]) == [ba.t("btn.edit")]
