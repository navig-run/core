"""A business-chat command word only acts when the message IS a command.

Audit 2026-10-03, against 15,110 real business messages: a command fired on the first
word alone. "Напомни пожалуйста во сколько у тебя дислокация" (the owner, in plain
conversation) would have been DELETED and replaced by the reminder usage line;
"Напомни 😁" from the other person got the usage line posted as the owner; and
"Weather is nice today" looks up the weather for whatever place the service makes of
"is nice today". "/" or "!" still means "talking to the bot", usage line and all.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

import navig.telegram.biz_commands as bc
import navig.telegram.biz_lookups as L
import navig.telegram.biz_reminders as r


class _Chan:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.send_message = AsyncMock()

    async def _api_call(self, method, data=None):
        await asyncio.sleep(0)
        self.calls.append((method, data or {}))
        return {"message_id": 500}

    def sent(self):
        return [d["text"] for m, d in self.calls if m == "sendMessage"]

    def deleted(self):
        return [i for m, d in self.calls if m == "deleteBusinessMessages" for i in d["message_ids"]]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "_path", lambda: tmp_path / "business_reminders.json")
    import navig.platform.paths as paths

    monkeypatch.setattr(paths, "cache_dir", lambda: tmp_path / "cache")
    yield
    for task in list(r._ARMED.values()):
        task.cancel()
    r._ARMED.clear()
    for task in list(bc._TIMERS.values()):
        task.cancel()
    bc._TIMERS.clear()


def _msg(text, *, owner=False):
    return {
        "chat": {"id": 555, "first_name": "Sam"},
        "message_id": 70,
        "business_connection_id": "bc1",
        "from": {"id": 777 if owner else 555},
        "text": text,
    }


async def _run(text, *, owner=False):
    ch = _Chan()
    handled = await bc.dispatch(ch, _msg(text, owner=owner), is_owner=owner, owner_id=777)
    return handled, ch


# ── conversation is left alone ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Напомни пожалуйста во сколько у тебя дислокация",  # a real owner message
        "Напомни 😁",  # a real message from the other person
        "Time to go",
        "Roll with it",
        "Flip it over",
        "Ping me later",
        "Weather is nice today",
        "Погода сегодня отличная",
        "Choice is yours",
        "Remember when we met?",
        "Timer is broken again",
        "Convert this file please",
        "Whois that guy",
        "Напомню тебе завтра",  # "I'll remind you" — the person, not the bot
    ],
)
@pytest.mark.parametrize("owner", [True, False], ids=["owner", "them"])
async def test_conversation_is_never_taken_for_a_command(text, owner):
    handled, ch = await _run(text, owner=owner)
    assert not handled, f"{text!r} was taken for a command"
    assert ch.sent() == [] and ch.deleted() == [], "nothing posted, nothing deleted"


# ── real commands still work, bare or prefixed ───────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "ping",
        "time",
        "flip",
        "dice",
        "roll 20",
        "8ball will it work?",
        "choice tea or coffee",
        "timer 5 min",
        "remind in 2 hours call",
        "remember in 15 hours schema",
        "Напомнить через 16 часов про схему",
        "Remember me about of Scheme 4 hours",
    ],
)
async def test_a_real_command_still_works_bare(text):
    handled, ch = await _run(text)
    assert handled and ch.sent(), text


@pytest.mark.parametrize("text", ["!напомнить", "/remind", "!weather is nice today"])
async def test_a_prefixed_word_always_answers_even_with_a_usage_line(text):
    handled, ch = await _run(text)
    assert handled and ch.sent()


async def test_the_owners_real_command_is_still_cleaned_up():
    handled, ch = await _run("ping", owner=True)
    assert handled and ch.deleted() == [70]


# ── Russian and French ───────────────────────────────────────────────────────


async def test_time_answers_in_the_language_that_asked(monkeypatch):
    _handled, ch = await _run("время")
    assert any(
        m in ch.sent()[-1]
        for m in (
            "января",
            "февраля",
            "марта",
            "апреля",
            "мая",
            "июня",
            "июля",
            "августа",
            "сентября",
            "октября",
            "ноября",
            "декабря",
        )
    )
    _handled, ch = await _run("heure")
    assert any(
        d in ch.sent()[-1]
        for d in ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
    )


async def test_a_coin_lands_in_russian():
    _handled, ch = await _run("монетка")
    assert ch.sent()[-1] in ("🪙 Орёл", "🪙 Решка")


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("таймер 5 минут", 300), ("minuteur 2 heures", 7200), ("timer 1h 30m", 5400)],
)
def test_the_timer_reads_russian_and_french_units(text, seconds):
    assert bc.parse_duration(text.split(None, 1)[1]) == seconds


async def test_a_russian_timer_starts():
    handled, ch = await _run("таймер 5 минут")
    assert handled and ch.sent()[-1] == "⏳ 05:00"


# ── whois and weather (audit 2026-10-03, run against the real services) ──────


_IANA_TLD_RECORD = """% IANA WHOIS server
refer:

domain:       RUN

organisation: Binky Moon, LLC
created:      2015-04-30
"""


async def test_whois_never_reports_the_tlds_own_record_as_the_domain(monkeypatch):
    """`.run` has no port-43 whois; IANA's empty `refer:` left its record about the
    TLD, and `whois navig.run` answered "Created: 2015-04-30" — the day `.run` was made."""
    monkeypatch.setattr(L, "_get_json", AsyncMock(return_value=None))  # no RDAP either
    monkeypatch.setattr(L, "_whois_raw", lambda d: _IANA_TLD_RECORD)
    out = await L.whois("navig.run")
    assert "2015" not in out and "No whois data" in out


async def test_whois_reads_rdap_first():
    rdap = {
        "ldhName": "NAVIG.RUN",
        "events": [
            {"eventAction": "registration", "eventDate": "2026-02-13T10:00:00Z"},
            {"eventAction": "expiration", "eventDate": "2027-02-13T10:00:00Z"},
        ],
        "entities": [
            {"roles": ["registrar"], "vcardArray": ["vcard", [["fn", {}, "text", "Porkbun LLC"]]]}
        ],
        "status": ["client delete prohibited"],
    }
    original = L._get_json
    L._get_json = AsyncMock(return_value=rdap)
    try:
        out = await L.whois("navig.run")
    finally:
        L._get_json = original
    assert "Porkbun LLC" in out and "2026-02-13" in out and "2027-02-13" in out
    assert "client delete prohibited" in out, "the whole status, not its first word"


async def test_a_free_domain_is_not_called_registered(monkeypatch):
    monkeypatch.setattr(L, "_get_json", AsyncMock(return_value=None))
    monkeypatch.setattr(L, "_whois_raw", lambda d: 'No match for "NOPE-123.COM".\n')
    assert "is not registered" in await L.whois("nope-123.com")


async def test_weather_answers_in_russian_and_keeps_the_city_name(monkeypatch):
    data = {
        "current_condition": [
            {
                "temp_C": "10",
                "FeelsLikeC": "8",
                "windspeedKmph": "10",
                "winddir16Point": "NNE",
                "humidity": "49",
                "weatherDesc": [{"value": "Clear"}],
                "lang_ru": [{"value": "Ясно"}],
            }
        ],
        "nearest_area": [
            {"areaName": [{"value": "Saint-Merri"}], "country": [{"value": "Russia"}]}
        ],
    }
    seen = {}

    async def fake(url, **_):
        seen["url"] = url
        return data

    monkeypatch.setattr(L, "_get_json", fake)
    out = await L.weather("москва", "ru")
    assert "lang=ru" in seen["url"]
    assert "Москва, Russia" in out and "Ясно" in out and "ощущается" in out
    assert "Saint-Merri" not in out
