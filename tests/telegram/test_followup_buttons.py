"""Telegram follow-up buttons: what a tapped suggestion produces, and how buttons look.

Regressions seen live (2026-10-04, a Russian chat about Fight Club):
- tapping a suggestion printed the raw ``EXPLORE_Q: a | b | c`` line at the end of the
  answer, because that path used a plain send that never parsed the marker;
- nothing showed the tap was being worked on;
- the Russian "Deeper" / "New ideas" labels rendered "�" (U+FFFD saved into the source);
- labels were cut mid-word with no ellipsis ("Чем книга отличается от филь").
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels import telegram_keyboards as kb
from navig.gateway.channels.telegram_keyboards import (
    CallbackHandler,
    ResponseKeyboardBuilder,
    button_label,
    extract_explore_questions,
)

RU_ANSWER = (
    "«Бойцовский клуб» — культовый фильм Дэвида Финчера по роману Чака Паланика. "
    "Рассказчик знакомится с Тайлером Дёрденом, и вместе они основывают клуб. "
) * 4  # a real answer: replies under 500 characters get no buttons by design


class _Store:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def put(self, key, value, **kw):
        self.data[key] = value


def _all_buttons(rows):
    return [b for row in rows or [] for b in row]


# ── labels and colours ───────────────────────────────────────


def test_russian_labels_carry_real_emoji():
    rows = ResponseKeyboardBuilder(_Store()).build(RU_ANSWER, "что такое бойцовский клуб", 1)
    texts = [b["text"] for b in _all_buttons(rows)]
    assert all("�" not in t for t in texts), texts
    assert any(t.startswith("📖 ") for t in texts) or any(t.startswith("🔍 ") for t in texts), texts


def test_a_long_label_ends_with_an_ellipsis_not_mid_word():
    long_q = "Чем книга отличается от фильма и почему это важно"
    label = button_label(long_q, kb.MAX_EXPLORE_BUTTON_TEXT)
    assert label.endswith("…") and len(label) <= kb.MAX_EXPLORE_BUTTON_TEXT
    assert not label[:-1].endswith(" ")
    assert button_label("Short one") == "Short one"


def test_suggestion_buttons_are_blue_and_full_length():
    qs = ["Объясни финальный твист", "Чем книга отличается от фильма"]
    rows = ResponseKeyboardBuilder(_Store()).build(RU_ANSWER, "u", 1, explore_questions=qs)
    suggestions = [row[0] for row in rows[1:]]
    assert [b["text"] for b in suggestions] == qs  # 31 chars fit now; were cut at 28
    assert {b.get("style") for b in suggestions} == {"primary"}
    assert all("style" not in b for b in rows[0]), "the secondary row stays neutral"


def test_approve_is_green_and_cancel_is_red():
    rows = ResponseKeyboardBuilder(_Store()).build(
        "Shall I restart nginx on the server?", "restart nginx", 1, profile_override="action"
    )
    styles = {b["callback_data"].split(":")[0]: b.get("style") for b in _all_buttons(rows)}
    assert styles.get("approve") == "success" and styles.get("cancel") == "danger"


# ── the marker ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "tail",
    [
        "EXPLORE_Q: Сравни концовку | Какие намёки были раньше",
        "**EXPLORE_Q:** Сравни концовку | Какие намёки были раньше",
        "<b>EXPLORE_Q:</b> Сравни концовку | Какие намёки были раньше",
        "`EXPLORE_Q:` Сравни концовку | **Какие намёки были раньше**",
    ],
)
def test_the_marker_is_stripped_in_every_decoration(tail):
    text, qs = extract_explore_questions(f"Финальный твист: это одно лицо.\n\n{tail}")
    assert "EXPLORE_Q" not in text
    assert text == "Финальный твист: это одно лицо."
    assert qs == ["Сравни концовку", "Какие намёки были раньше"]


# ── tapping a suggestion ─────────────────────────────────────


class _Channel:
    """A channel WITHOUT the streaming helpers — exercises the plain fallback."""

    def __init__(self):
        self.sent: list[dict] = []
        self.edits: list[dict] = []

    async def send_message(self, chat_id, text, parse_mode="HTML", keyboard=None, **kw):
        self.sent.append({"text": text, "keyboard": keyboard})
        return {"message_id": 100 + len(self.sent)}

    async def edit_message(self, chat_id, message_id, text, parse_mode="HTML", keyboard=None):
        self.edits.append({"id": message_id, "text": text, "keyboard": keyboard})
        return {"message_id": message_id}

    async def _keep_typing(self, chat_id):
        await asyncio.Event().wait()


class _FinalizingChannel(_Channel):
    """The real channel's shape: the placeholder is handed to _finalize_streamed_message."""

    def __init__(self):
        super().__init__()
        self.finalized: list[dict] = []

    async def _finalize_streamed_message(self, chat_id, placeholder_id, response, **kw):
        self.finalized.append({"id": placeholder_id, "response": response, **kw})
        return True

    async def _send_response(self, *a, **kw):
        raise AssertionError("must not double-send when the placeholder was finalized")


def _handler(channel, reply):
    h = CallbackHandler.__new__(CallbackHandler)
    h.channel = channel
    h.store = _Store()
    h.prompts = []
    h.events = []  # StatusEvents the agent "emits" while answering

    async def _get_ai_response(prompt, user_id, *, status_sink=None):
        h.prompts.append(prompt)
        for event in h.events:
            await status_sink(event)
        return reply

    h._get_ai_response = _get_ai_response
    return h


REPLY_BODY = ("Рассказчик и Тайлер — одно лицо. Всё, что происходило «с Тайлером», делал сам "
              "Рассказчик в состоянии раздвоения. ") * 5
REPLY = REPLY_BODY.rstrip() + "\n\nEXPLORE_Q: Сравни концовку книги и фильма | Какие намёки были раньше"


def test_a_tapped_suggestion_shows_a_thinking_bubble_first():
    ch = _FinalizingChannel()
    asyncio.run(_handler(ch, REPLY)._ask_followup(1, 2, "Объясни финальный твист"))
    assert ch.sent and ch.sent[0]["text"].startswith("💭 ") and "Объясни финальный твист" in ch.sent[0]["text"]


def test_the_bubble_is_finalized_like_a_typed_reply():
    ch = _FinalizingChannel()
    h = _handler(ch, REPLY)
    asyncio.run(h._ask_followup(1, 2, "Объясни финальный твист"))
    (fin,) = ch.finalized
    assert fin["id"] == 101 and fin["original_text"] == "Объясни финальный твист"
    assert "EXPLORE_Q" in h.prompts[0], "a tapped suggestion must ask for its own follow-ups"


def test_without_the_streaming_helpers_the_marker_still_becomes_buttons():
    ch = _Channel()
    asyncio.run(_handler(ch, REPLY)._ask_followup(1, 2, "Объясни финальный твист"))
    (final,) = ch.edits  # the placeholder was EDITED into the answer
    assert "EXPLORE_Q" not in final["text"]
    labels = [b["text"] for b in _all_buttons(final["keyboard"])]
    assert "Сравни концовку книги и фильма" in labels and "Какие намёки были раньше" in labels


def test_a_failed_answer_replaces_the_bubble_instead_of_leaving_it():
    ch = _Channel()
    asyncio.run(_handler(ch, None)._ask_followup(1, 2, "Объясни финальный твист"))
    assert ch.edits and ch.edits[0]["text"].startswith("❌")


def test_a_tap_that_runs_tools_shows_live_status_in_its_bubble(monkeypatch):
    """2026-10-16: a tapped suggestion ran bash for 90 s behind a static bubble —
    typed messages show "Thinking… / Running a command…", taps showed nothing."""
    from navig.agent.conv.status_event import StatusEvent
    from navig.gateway.channels import telegram_progress

    # Real steps are seconds apart; the 0.5 s edit throttle would drop the 2nd here.
    monkeypatch.setattr(telegram_progress, "_MIN_EDIT_INTERVAL", 0.0)

    ch = _FinalizingChannel()
    h = _handler(ch, REPLY)
    def _ev(etype, **meta):
        return StatusEvent(type=etype, task_id="t", timestamp=0.0, message="", metadata=meta)

    h.events = [
        _ev("thinking", step_index=0),
        _ev("step_start", tool="bash_exec", args_summary="", step_index=1),
    ]
    asyncio.run(h._ask_followup(1, 2, "Объясни финальный твист"))
    # Every status edit lands on the ONE bubble the tap created — no extra messages.
    assert len(ch.sent) == 1 and {e["id"] for e in ch.edits} == {101}
    last = ch.edits[-1]["text"]
    assert "Объясни финальный твист" in last, "the tapped question stays on top"
    assert "Running a command" in last
    (fin,) = ch.finalized
    assert fin["id"] == 101, "the answer is still edited into the same bubble"


def test_the_toast_names_the_tapped_question():
    ch = _FinalizingChannel()
    h = _handler(ch, REPLY)
    h.store.put("followup:k", "Объясни финальный твист")
    h._answered_callback_ids = set()
    h._jobs_inflight = set()
    toasts = []

    async def _answer(cb_id, text="", show_alert=False):
        toasts.append(text)

    h._answer = _answer
    asyncio.run(h._handle_ask_followup_callback(cb_id="c", chat_id=1, user_id=2, followup_key="followup:k"))
    assert toasts == ["💭 Объясни финальный твист"]


# ── history ──────────────────────────────────────────────────


def test_history_never_stores_the_raw_marker():
    from navig.gateway.channels import telegram as tg

    recorded = []

    class _SM:
        def add_assistant_message(self, **kw):
            recorded.append(kw["text"])

    if not tg.HAS_SESSIONS:
        pytest.skip("sessions unavailable in this build")
    ch = tg.TelegramChannel.__new__(tg.TelegramChannel)
    ch._record_assistant_msg(object(), _SM(), 1, 2, REPLY, False)
    assert recorded == [REPLY_BODY.rstrip()]


# ── fallbacks in the reply's language · medium replies · agent history ───────

RU_MEDIUM = (
    "Финал раскрывает, что Тайлер — это сам рассказчик. Все сцены с ним "
    "оказываются проекцией, а взрывы — его собственный план. "
) * 3  # ~330 characters: under the 500 that earns buttons without suggestions


def _explore_texts(rows):
    return [b["text"] for b in _all_buttons(rows) if b.get("style") == kb.STYLE_PRIMARY]


def test_a_russian_reply_gets_russian_fallback_suggestions():
    rows = ResponseKeyboardBuilder(_Store()).build(
        RU_ANSWER, "что такое бойцовский клуб", 1, explore_questions=["Кто режиссёр?"]
    )
    texts = _explore_texts(rows)
    assert "Кто режиссёр?" in texts
    assert len(texts) >= kb._EXPLORE_Q_MIN, texts
    assert all(not any("a" <= c.lower() <= "z" for c in t) for t in texts), texts


def test_an_english_reply_keeps_english_fallbacks(monkeypatch):
    from navig.core import i18n

    monkeypatch.setattr(i18n, "current_language", lambda: "ru")  # Russian operator, English reply
    answer = "Fight Club is a 1999 film directed by David Fincher. " * 12
    rows = ResponseKeyboardBuilder(_Store()).build(answer, "what is fight club", 1, explore_questions=["Who stars?"])
    texts = _explore_texts(rows)
    assert len(texts) >= kb._EXPLORE_Q_MIN and all(t.isascii() for t in texts), texts


def test_a_latin_reply_follows_the_operators_language(monkeypatch):
    from navig.core import i18n

    monkeypatch.setattr(i18n, "current_language", lambda: "fr")
    assert kb._explore_fallbacks(None) == i18n.shared().pool(kb._EXPLORE_FALLBACKS_KEY, lang="fr")
    assert kb._explore_fallbacks(None) != list(kb._EXPLORE_FALLBACKS_EN)


def test_a_missing_locale_pool_still_gives_fallbacks(monkeypatch):
    from navig.core import i18n

    monkeypatch.setattr(i18n.Translator, "pool", lambda self, key, lang=None: [])
    assert kb._explore_fallbacks("ru") == list(kb._EXPLORE_FALLBACKS_EN)


def test_a_medium_reply_with_suggestions_shows_them():
    assert 250 < len(RU_MEDIUM) < 500
    rows = ResponseKeyboardBuilder(_Store()).build(
        RU_MEDIUM, "объясни финальный твист", 1, explore_questions=["Почему он не замечал?", "Книга vs фильм"]
    )
    assert "Почему он не замечал?" in _explore_texts(rows)


def test_a_medium_reply_without_suggestions_stays_bare():
    assert ResponseKeyboardBuilder(_Store()).build(RU_MEDIUM, "объясни финальный твист", 1) is None


def test_a_short_ack_with_suggestions_stays_bare():
    rows = ResponseKeyboardBuilder(_Store()).build("Готово!", "сделай", 1, explore_questions=["Ещё?"])
    assert rows is None


def test_agent_history_never_stores_the_raw_marker(tmp_path, monkeypatch):
    from navig.agent.conv import history as hist

    monkeypatch.setattr(hist, "config_dir", lambda: tmp_path)
    h = hist.ConversationHistory("u1")
    h.add("assistant", "Тайлер — это рассказчик.\nEXPLORE_Q: Почему? | Книга vs фильм")
    h.add("user", "EXPLORE_Q: typed by the user stays as typed")
    import json

    lines = (tmp_path / "history" / "u1.jsonl").read_text(encoding="utf-8").splitlines()
    stored = [json.loads(ln)["content"] for ln in lines]
    assert stored == ["Тайлер — это рассказчик.", "EXPLORE_Q: typed by the user stays as typed"]
    assert h.get_messages()[0]["content"] == "Тайлер — это рассказчик."
