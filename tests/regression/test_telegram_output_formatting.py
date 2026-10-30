"""Telegram output formatting — four things the operator saw arrive raw (2026-10).

A. The heartbeat alert: ``[!] Health check found issues:`` / ``- [HIGH] LLM mode
   'big_tasks' → anthropic:claude-opus-4-8 is auth (auth failed (401 …))`` — no
   emoji, no structure, and "is auth" for grammar. The notify router's direct
   Bot-API path escaped the body instead of rendering it.
B. An AI reply ending in a raw ``EXPLORE_Q: Сравни концовку книги и фильма | …``
   line instead of explore buttons: the streamed partial carried the marker, a
   failed final edit still reported success, and case-sensitive pre-checks
   disagreed with the (case-insensitive) extractor.
C. A reminder delivered as one escaped blob (and a double ⏰).
D. Sales rings, the daily sales summary and briefings were never tidied away
   overnight: the router's Telegram send never ran inside ``chat_tidy.routine()``.

Every Telegram call is mocked; nothing here can reach the live daemon or the
operator's real config (NAVIG_CONFIG_DIR / NAVIG_DATA_DIR are a tmp dir).
"""

from __future__ import annotations

import asyncio
from html.parser import HTMLParser
from unittest.mock import AsyncMock

import pytest

OPERATOR_ISSUE = (
    "[HIGH] LLM mode 'big_tasks' → anthropic:claude-opus-4-8 is auth "
    "(auth failed (401 — key/token invalid or expired)). "
    "Fix: navig mode set big_tasks --provider <p> --model <m>"
)

# Tags Telegram's HTML parse mode accepts. Anything else (or anything unbalanced)
# makes Telegram reject the message, and the send falls back to plain text.
_TG_TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre",
            "a", "blockquote", "tg-spoiler", "span"}


class _Balance(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in _TG_TAGS:
            self.errors.append(f"unsupported <{tag}>")
        self.stack.append(tag)
        self.tags.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"</{tag}> closes {self.stack[-1:] or 'nothing'}")
        else:
            self.stack.pop()


def assert_telegram_html(text: str) -> list[str]:
    p = _Balance()
    p.feed(text)
    p.close()
    assert not p.errors and not p.stack, f"malformed Telegram HTML {p.errors} {p.stack}: {text!r}"
    return p.tags


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    from navig.config import reset_config_manager

    reset_config_manager()
    yield
    reset_config_manager()


# ── A. heartbeat alert ──────────────────────────────────────────────────────


def test_heartbeat_alert_renders_as_a_card_not_raw_tokens():
    from navig.heartbeat.alert_text import issues_alert
    from navig.notify.router import format_telegram_html

    title, body = issues_alert([OPERATOR_ISSUE])
    html = format_telegram_html("system_alert", title, body)

    tags = assert_telegram_html(html)
    assert html.startswith("<b>⚠️ Health check: 1 problem</b>")
    assert "🔴" in html
    assert "<code>anthropic:claude-opus-4-8</code>" in html
    assert "<code>big_tasks</code>" in html
    assert "<code>navig mode set big_tasks --provider &lt;p&gt; --model &lt;m&gt;</code>" in html
    assert "key rejected (401 — key/token invalid or expired)" in html
    for raw in ("[!]", "[HIGH]", "is auth", "Health check found issues"):
        assert raw not in html, raw
    assert "i" not in tags, f"`big_tasks` must not open italics: {html!r}"


def test_issue_record_itself_is_unchanged_for_triage():
    """`issues_found` is parsed by triage — the human wording must not leak back."""
    from navig.heartbeat.triage import is_provider_condition

    assert is_provider_condition(OPERATOR_ISSUE)


@pytest.mark.parametrize(
    "issue, expect",
    [
        ("[HIGH] routing tier 'coder_big' → nvidia:x/y is dead (model retired (410 EOL)). "
         "Fix: navig mode route set coder_big --provider <p> --model <m>",
         "Tier `coder_big` → `nvidia:x/y` — model retired (410 EOL)"),
        ("[HIGH] groq's first catalog model → groq:llama is unreachable "
         "(endpoint unreachable / timed out). Fix: navig ai models --check --provider groq",
         "`groq` catalog → `groq:llama` — unreachable (endpoint unreachable / timed out)"),
        ("[HIGH] Disk / is 97% full", "🔴 Disk / is 97% full"),
        ("Backup is 3 days old", "• Backup is 3 days old"),
    ],
)
def test_humanize_issue_shapes(issue, expect):
    from navig.heartbeat.alert_text import humanize_issue

    assert expect in humanize_issue(issue)


def test_router_fallback_sends_the_formatted_card(monkeypatch):
    from navig.gateway import notifications
    from navig.messaging import notify_operator as no
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter

    sent: list[str] = []
    monkeypatch.setattr(no, "notify_operator", lambda text, **kw: sent.append(text) or True)
    _telegram_only(monkeypatch, prefs, notifications)

    from navig.heartbeat.alert_text import issues_alert

    title, body = issues_alert([OPERATOR_ISSUE])
    out = asyncio.run(NotificationRouter().dispatch("system_alert", title, body))
    assert out["channels"] == [{"channel": "telegram", "ok": True, "detail": "sent"}]
    assert sent and "<code>big_tasks</code>" in sent[0] and "[HIGH]" not in sent[0]
    assert_telegram_html(sent[0])


def test_title_with_its_own_emoji_gets_no_second_one():
    from navig.notify.router import format_telegram_html

    assert format_telegram_html("sale", "💰 New sale: Pro", "$4.99") == (
        "<b>💰 New sale: Pro</b>\n\n$4.99"
    )
    assert format_telegram_html("briefing", "Briefing · Thu", "**3** tasks").startswith(
        "☀️ <b>Briefing · Thu</b>"
    )


def test_md_to_html_keeps_snake_case_and_code_spans_intact():
    from navig.gateway.channels.telegram_html import md_to_html

    out = md_to_html("run `navig mode set big_tasks` then `x_y`; daily_clean stays")
    assert_telegram_html(out)
    assert out == (
        "run <code>navig mode set big_tasks</code> then <code>x_y</code>; daily_clean stays"
    )
    assert md_to_html("_real italics_ here") == "<i>real italics</i> here"


# ── B. EXPLORE_Q marker ─────────────────────────────────────────────────────

RU_ITEMS = ["Сравни концовку книги и фильма", "Какие намёки были раньше", "Кто режиссёр"]


@pytest.mark.parametrize(
    "tail",
    [
        "EXPLORE_Q: Сравни концовку книги и фильма | Какие намёки были раньше | Кто режиссёр",
        "Explore_Q: Сравни концовку книги и фильма | Какие намёки были раньше | Кто режиссёр",
        "EXPLORE_Q : Сравни концовку книги и фильма | Какие намёки были раньше | Кто режиссёр",
        "EXPLORE_Q\uff1a Сравни концовку книги и фильма | Какие намёки были раньше | Кто режиссёр",
        "EXPLORE\\_Q: Сравни концовку книги и фильма | Какие намёки были раньше | Кто режиссёр",
        "**EXPLORE_Q**: Сравни концовку книги и фильма | Какие намёки были раньше | Кто режиссёр",
        "`EXPLORE_Q:` [Сравни концовку книги и фильма] | [Какие намёки были раньше] | [Кто режиссёр]",
        "EXPLORE_Q:\n- Сравни концовку книги и фильма\n- Какие намёки были раньше\n- Кто режиссёр",
        "EXPLORE_Q:\n1. Сравни концовку книги и фильма\n2) Какие намёки были раньше\nКто режиссёр",
    ],
)
def test_extractor_handles_the_marker_variants(tail):
    from navig.gateway.channels.telegram_keyboards import extract_explore_questions

    text, qs = extract_explore_questions(f"Финал фильма отличается от книги.\n\n{tail}")
    assert text == "Финал фильма отличается от книги."
    assert qs == RU_ITEMS


def test_explore_button_keys_stay_ascii_and_short_for_cyrillic():
    """callback_data is capped at 64 BYTES — the question lives in the store, not the key."""
    from navig.gateway.channels.telegram_keyboards import ResponseKeyboardBuilder

    class _Store:
        def __init__(self):
            self.data = {}

        def put(self, k, v, **kw):
            self.data[k] = v

        def get(self, k):
            return self.data.get(k)

    b = ResponseKeyboardBuilder(store=_Store())
    rows = b.build(ai_response="Ответ. " * 100, user_message="вопрос", message_id=1,
                   explore_questions=RU_ITEMS)
    cbs = [btn["callback_data"] for row in rows for btn in row]
    assert cbs and all(cb.isascii() and len(cb.encode()) <= 64 for cb in cbs)
    stored = [v for v in b.store.data.values() if isinstance(v, str)]
    assert set(RU_ITEMS) <= set(stored)


@pytest.mark.parametrize(
    "partial, visible",
    [
        ("Ответ.\nEXPLORE_Q: а | б", "Ответ."),
        ("Ответ.\nEXPLO", "Ответ."),
        ("Ответ.\n**EXPLORE_Q", "Ответ."),
        ("Ответ.\nExample", "Ответ.\nExample"),
        ("Ответ.", "Ответ."),
    ],
)
def test_streaming_view_never_shows_the_marker(partial, visible):
    from navig.gateway.channels.telegram_keyboards import hide_explore_partial

    assert hide_explore_partial(partial) == visible


class _ExploreKb:
    """Keyboard builder stand-in: one row per explore question it was handed."""

    def __init__(self):
        self.seen: list[list[str]] = []

    def build(self, *, explore_questions=None, **_kw):
        qs = list(explore_questions or [])
        self.seen.append(qs)
        return [[{"text": q, "callback_data": f"eq{i}"}] for i, q in enumerate(qs)]


def _channel(monkeypatch):
    from navig.gateway.channels import telegram as tg
    from navig.gateway.channels.telegram import TelegramChannel

    ch = TelegramChannel(bot_token="123:FAKE")
    ch._maybe_send_voice = AsyncMock(return_value=False)
    ch._rich_messages_enabled = lambda: False
    ch._kb_builder = _ExploreKb()
    monkeypatch.setattr(tg, "HAS_TEMPLATES", False)
    return ch


ANSWER = "Финал фильма отличается от книги."
REPLY = ANSWER + "\nExplore_Q: " + " | ".join(RU_ITEMS)


async def test_finalize_reports_failure_when_both_edits_fail(monkeypatch):
    ch = _channel(monkeypatch)
    calls: list[tuple[str, dict]] = []

    async def _api(method, data):
        calls.append((method, data))
        return None if method == "editMessageText" else {"message_id": 5}

    ch._api_call = _api
    used = await ch._finalize_streamed_message(1, 99, REPLY, original_text="q")
    assert used is False, "a bubble that could not be edited must not be reported as finalized"


async def test_stream_reply_never_shows_marker_and_falls_back_with_buttons(monkeypatch):
    from navig.gateway.channels import telegram as tg

    ch = _channel(monkeypatch)
    calls: list[tuple[str, dict]] = []

    async def _api(method, data):
        calls.append((method, dict(data)))
        if method == "editMessageText":
            return None  # every edit is rejected
        return {"message_id": 77}

    ch._api_call = _api
    ch.send_typing = AsyncMock()

    clock = iter(range(0, 10_000, 5))

    class _Time:
        def __getattr__(self, name):
            import time as _t

            return getattr(_t, name)

        @staticmethod
        def monotonic():
            return float(next(clock))

    monkeypatch.setattr(tg, "time", _Time())

    async def _on_message(*, on_partial=None, **_kw):
        acc = ""
        for chunk in [ANSWER, "\nExpl", "ore_Q: ", RU_ITEMS[0], " | " + RU_ITEMS[1],
                      " | " + RU_ITEMS[2]]:
            acc += chunk
            if on_partial:
                await on_partial(acc)
        return acc

    ch.on_message = _on_message
    out = await ch._stream_reply(1, 7, "q", {}, original_text="q", is_group=False)
    assert out == REPLY

    shown = [d.get("text", "") for m, d in calls if m in ("sendMessage", "editMessageText")]
    assert shown, calls
    assert not [t for t in shown if "xpl" in t.lower()], f"marker leaked: {shown!r}"
    # Both edits failed → the placeholder is deleted and the reply sent fresh, WITH buttons.
    assert ("deleteMessage", {"chat_id": 1, "message_id": 77}) in [
        (m, {k: d[k] for k in ("chat_id", "message_id") if k in d}) for m, d in calls
    ]
    final = [d for m, d in calls if m == "sendMessage" and "reply_markup" in d]
    assert final, calls
    labels = [b["text"] for row in final[-1]["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == RU_ITEMS
    spoken = ch._maybe_send_voice.await_args.args[3]
    assert "EXPLORE" not in spoken.upper()


async def test_finalize_voice_gets_the_cleaned_text(monkeypatch):
    ch = _channel(monkeypatch)

    async def _api(method, data):
        return {"message_id": 5}

    ch._api_call = _api
    assert await ch._finalize_streamed_message(1, 99, REPLY, original_text="q") is True
    assert ch._maybe_send_voice.await_args.args[3] == ANSWER


def test_strip_explore_marker_for_button_less_paths():
    from navig.gateway.channels.telegram_keyboards import strip_explore_marker

    assert strip_explore_marker(REPLY) == ANSWER
    assert strip_explore_marker("no marker") == "no marker"
    assert strip_explore_marker("") == ""


# ── C. reminder card ────────────────────────────────────────────────────────

HEADER = "⏰ <b>Rappel</b>"
BLOB = (
    "RDV Dr ALBANE GIREL AUJOURD HUI 11h40 - Centre d'imagerie, 12 rue de la Paix. "
    "LE MOTIF: l IRM du genou gauche. Fiche: ops/rdv-2026-10-07-dr-girel.md"
)


def test_reminder_card_for_a_long_pasted_message():
    from navig.telegram.reminder_card import render

    html = render(HEADER, BLOB)
    assert_telegram_html(html)
    title_line = html.split("\n")[0]
    assert title_line == "<b>RDV Dr ALBANE GIREL AUJOURD HUI 11h40</b>"
    assert "<b>Fiche:</b> ops/rdv-2026-10-07-dr-girel.md" in html  # nothing is lost
    assert html.count("⏰") == 1


def test_reminder_card_for_a_todo_ping_new_and_old_rows():
    from navig.telegram.reminder_card import render

    new = render(HEADER, "Dentist — 1 hour before (due today 11:40)")
    old = render(HEADER, "⏰ Dentist — 1 hour before (due today 11:40)")
    assert new == old == (
        "<b>Dentist</b>\n<i>⏰ Rappel · 1 hour before · due today 11:40</i>"
    )
    assert render(HEADER, "⏰ Dentist — due now (today 11:40)").endswith(
        "<i>⏰ Rappel · due now · today 11:40</i>"
    )


def test_reminder_card_escapes_and_renders_markdown_details():
    from navig.telegram.reminder_card import render

    html = render(HEADER, "Call <Bob> & co\n**bring** the `a_b` file")
    assert_telegram_html(html)
    assert "<b>Call &lt;Bob&gt; &amp; co</b>" in html
    assert "<b>bring</b> the <code>a_b</code> file" in html


def test_reminder_text_has_no_second_alarm_clock():
    from datetime import datetime, timedelta, timezone

    from navig.pim import reminders

    now = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    todo = {"title": "Dentist", "due_at": (now + timedelta(hours=1)).isoformat()}
    text = reminders.reminder_text(todo, "1h", now, now)
    assert not text.startswith("⏰") and text.startswith("Dentist — ")


def test_appointment_title_is_a_short_subject():
    from datetime import datetime, timedelta, timezone

    from navig.telegram import appointments

    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone(timedelta(hours=2)))
    text = (
        "RDV Dr ALBANE GIREL le mercredi 7 octobre 2026 à 11h40 - Centre d'imagerie, "
        "12 rue de la Paix. LE MOTIF: l IRM du genou gauche. "
        "Fiche: ops/rdv-2026-10-07-dr-girel.md"
    )
    hit = appointments.detect(text, now)
    assert hit is not None
    assert hit[1] == "RDV Dr ALBANE GIREL"


def test_headline_without_a_boundary_is_cut_on_a_word():
    from navig.telegram.reminder_card import split_headline

    head, rest = split_headline("word " * 30)
    assert len(head) <= 60 and head.endswith("…") and not head.endswith(" …")
    assert rest.startswith("word word")


# ── D. nightly tidy covers sales + briefings ────────────────────────────────


def _telegram_only(monkeypatch, prefs, notifications):
    monkeypatch.setattr(prefs, "get_settings", lambda: {
        "master_enabled": True, "quiet_hours_enabled": False,
        "quiet_hours_start": 22, "quiet_hours_end": 7,
    })
    monkeypatch.setattr(prefs, "enabled_channels", lambda _t: ["telegram"])

    class _NM:
        def get_channel(self, _name):
            return None

    monkeypatch.setattr(notifications, "get_notification_manager", lambda: _NM())


@pytest.fixture
def tidy(monkeypatch):
    from navig.telegram import chat_tidy

    cfg = {"enabled": True, "hour": 4, "scope": "routine",
           "routine_types": chat_tidy.DEFAULT_ROUTINE_NOTIFY_TYPES}
    monkeypatch.setattr(chat_tidy, "settings", lambda fresh=False: cfg)
    return cfg


def _dispatch_and_ledger(monkeypatch, type_key: str) -> list[int]:
    import itertools
    import time

    from navig.gateway import notifications
    from navig.messaging import notify_operator as no
    from navig.notify import prefs
    from navig.notify.router import NotificationRouter
    from navig.telegram import chat_tidy

    ids = itertools.count(100)

    def _send(text, **kw):
        # What commands.telegram._api_call does with the Message Telegram returns —
        # in the worker thread, where only a COPIED context carries the routine flag.
        chat_tidy.note({"message_id": next(ids), "date": int(time.time()),
                        "chat": {"id": 4242, "type": "private"}})
        return True

    monkeypatch.setattr(no, "notify_operator", _send)
    _telegram_only(monkeypatch, prefs, notifications)
    asyncio.run(NotificationRouter().dispatch(type_key, "t", "b"))
    path = chat_tidy.ledger_path()
    return [e["m"] for e in chat_tidy.read_ledger(path)] if path.exists() else []


@pytest.mark.parametrize("type_key", ["sale", "sales_digest", "briefing"])
def test_routine_notifications_land_in_the_tidy_ledger(monkeypatch, tidy, type_key):
    assert _dispatch_and_ledger(monkeypatch, type_key) == [100]


@pytest.mark.parametrize("type_key", ["system_alert", "sales_check_failed", "security_alert"])
def test_alerts_are_never_tidied(monkeypatch, tidy, type_key):
    assert _dispatch_and_ledger(monkeypatch, type_key) == []


def test_routine_types_are_configurable(monkeypatch, tidy):
    from navig.telegram import chat_tidy

    tidy["routine_types"] = chat_tidy._parse_types("sale, deploy")
    assert chat_tidy.is_routine_notification("deploy")
    assert not chat_tidy.is_routine_notification("briefing")
    assert chat_tidy._parse_types(["a", "b"]) == frozenset({"a", "b"})
    assert chat_tidy._parse_types(None) is None


# ── localized pings render as cards too ──────────────────────────────────────


@pytest.mark.parametrize(
    ("msg", "title", "when"),
    [
        ("Dentist — 1 hour before (due today 11:40)", "Dentist", "1 hour before · due today 11:40"),
        ("Dentist — due now (today 11:40)", "Dentist", "due now · today 11:40"),
        ("Стоматолог — за 3 дн (Пн 26 окт · 10:30)", "Стоматолог", "за 3 дн · Пн 26 окт · 10:30"),
        ("Dentiste — c'est l'heure (lun 12:00)", "Dentiste", "c'est l'heure · lun 12:00"),
        ("⏰ Dentist — 1 hour before (due today 11:40)", "Dentist", "1 hour before · due today 11:40"),
    ],
)
def test_reminder_card_parses_every_locale(msg, title, when):
    from navig.telegram.reminder_card import render

    html = render(HEADER, msg)
    assert_telegram_html(html)
    lines = html.split("\n")
    # The caption no longer takes a stanza of its own above the headline: it rides
    # the "when" line, because "⏰ Rappel" over "Dentist" said what the notification
    # already said.
    assert lines[0] == f"<b>{title}</b>"
    assert lines[1] == f"<i>⏰ Rappel · {when}</i>"
    assert html.count("⏰") == 1


def test_a_long_parenthetical_in_a_pasted_note_is_not_a_ping():
    from navig.telegram.reminder_card import _parts

    note = "Call the insurer — ask about the claim and the second invoice they sent (ref 4471)"
    title, when, _details = _parts(note)
    assert when == ""


def test_stored_reminder_text_has_no_clock_in_any_locale():
    import json
    from pathlib import Path

    import navig

    root = Path(navig.__file__).parent / "locales"
    for lang in ("en", "fr", "ru"):
        data = json.loads((root / f"{lang}.json").read_text(encoding="utf-8"))
        for key in ("pim.ping.lead", "pim.ping.now"):
            assert not data[key].startswith("⏰"), (lang, key)


# ── a dense pasted note reads as labelled lines ──────────────────────────────

LABELLED = (
    "RDV Dr GIREL 11h40 - Centre Jacques Coeur. LE MOTIF: une IRM. "
    "LES 4 QUESTIONS: 1) et maintenant? EMPORTER: IRM, bilan. Fiche: ops/rdv.md"
)


def test_a_note_with_several_labels_gets_one_bold_line_per_label():
    from navig.telegram.reminder_card import render

    html = render(HEADER, LABELLED)
    assert_telegram_html(html)
    lines = html.split("\n")
    for label in ("LE MOTIF:", "LES 4 QUESTIONS:", "EMPORTER:", "Fiche:"):
        assert any(line.startswith(f"<b>{label}</b> ") for line in lines), label


def test_prose_with_a_single_colon_is_left_as_one_paragraph():
    from navig.telegram.reminder_card import labelled_lines

    prose = "Call the bank about the card: they said Monday. Bring ID."
    assert labelled_lines(prose) == prose
    assert labelled_lines("Line one.\nAlready: structured. Keep: it.") == (
        "Line one.\nAlready: structured. Keep: it."
    )


async def test_stream_reply_logs_time_to_first_token_once(monkeypatch, navig_log_capture):
    """Time-to-first-token is logged once per reply — on the first token, not every edit.

    TTFT is the latency a user actually feels; a reply's total time hides
    whether a long answer started promptly. One line per reply keeps it
    measurable without flooding the log on every streamed edit.
    """
    import re

    from navig.gateway.channels import telegram as tg

    ch = _channel(monkeypatch)

    async def _api(method, data):
        return {"message_id": 77}

    ch._api_call = _api
    ch.send_typing = AsyncMock()

    clock = iter(range(0, 10_000, 5))

    class _Time:
        def __getattr__(self, name):
            import time as _t

            return getattr(_t, name)

        @staticmethod
        def monotonic():
            return float(next(clock))

    monkeypatch.setattr(tg, "time", _Time())

    async def _on_message(*, on_partial=None, **_kw):
        acc = ""
        for chunk in ["Hel", "lo wor", "ld, this is a streamed reply."]:
            acc += chunk
            if on_partial:
                await on_partial(acc)
        return acc

    ch.on_message = _on_message
    await ch._stream_reply(1, 7, "q", {}, original_text="q", is_group=False)

    ttft = [m for m in navig_log_capture if m.startswith("telegram reply first token after")]
    assert len(ttft) == 1, navig_log_capture
    assert re.fullmatch(r"telegram reply first token after \d+ms \(deep=False\)", ttft[0]), ttft
