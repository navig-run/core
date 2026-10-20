"""Proactive alerts reach the operator — once each — and the watcher starts by itself.

Before: ``proactive:alert`` hooks fired on every 60-second poll and nothing listened,
so an upcoming meeting or a new email reached nobody; and the engine only ran if
someone called ``POST /proactive/start``. These pin delivery through the notify router,
the once-per-alert ledger (across polls AND restarts), and the boot-time auto-start
that only happens when there is a real calendar or mailbox to watch.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from navig.agent.proactive.alert_ledger import AlertLedger
from navig.agent.proactive.engine import ProactiveEngine
from navig.agent.proactive.providers import CalendarEvent, EmailMessage


class _Calendar:
    def __init__(self, events):
        self.events = events

    async def list_events(self, start, end):
        return self.events


class _Mailbox:
    def __init__(self, messages):
        self.messages = messages

    async def list_unread(self, limit=5):
        return self.messages


@pytest.fixture
def sent(monkeypatch):
    out: list[tuple] = []

    async def fake_dispatch(type_key, title, body="", **kw):
        out.append((type_key, title, body))
        return {}

    monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)
    return out


def _engine(tmp_path, *, events=(), messages=()):
    eng = ProactiveEngine()
    eng.trigger_manager = SimpleNamespace(process_event=lambda e: None)
    eng._ledger = AlertLedger(tmp_path / "alerted.json")
    if events:
        eng.set_calendar_provider(_Calendar(list(events)))
    if messages:
        eng.email_providers = {"work": _Mailbox(list(messages))}
    return eng


def _event():
    start = datetime.now() + timedelta(minutes=30)
    return CalendarEvent(id="ev1", title="Dentist", start=start, end=start + timedelta(hours=1),
                         location="Main St")


def _mail(mid="m1"):
    return EmailMessage(id=mid, subject="Invoice #42", sender="billing@example.com",
                        snippet="Your invoice is ready", received_at=datetime.now())


async def test_each_alert_is_delivered_once_across_polls(tmp_path, sent):
    eng = _engine(tmp_path, events=[_event()], messages=[_mail()])

    for _ in range(3):
        await eng.run_checks(None)

    types = sorted(t for t, _, _ in sent)
    assert types == ["email_new", "reminder"]
    reminder = next(s for s in sent if s[0] == "reminder")
    assert "Dentist" in reminder[1] and "Main St" in reminder[2]


async def test_a_restart_does_not_re_announce(tmp_path, sent):
    await _engine(tmp_path, messages=[_mail()]).run_checks(None)
    await _engine(tmp_path, messages=[_mail(), _mail("m2")]).run_checks(None)

    assert [t for t, _, _ in sent] == ["email_new", "email_new"]  # m1 once, m2 once


async def test_mock_providers_alert_nothing(tmp_path, sent):
    eng = _engine(tmp_path)
    await eng.run_checks(None)
    assert sent == [] and eng.has_real_provider() is False


def test_real_provider_detection(tmp_path):
    assert _engine(tmp_path, events=[_event()]).has_real_provider() is True
    assert _engine(tmp_path, messages=[_mail()]).has_real_provider() is True


def test_ledger_never_overwrites_a_file_it_could_not_read(tmp_path, monkeypatch):
    path = tmp_path / "alerted.json"
    path.write_text(json.dumps({"old": 1.0}), encoding="utf-8")

    def locked(p, *a, **kw):
        raise PermissionError("locked by another process")

    monkeypatch.setattr("navig.core.json_io.read_text_retrying", locked)
    ledger = AlertLedger(path)
    assert ledger.claim("k") is True
    assert ledger.claim("k") is False  # still deduped in memory
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": 1.0}


def test_a_corrupt_ledger_is_kept_aside_not_lost(tmp_path):
    path = tmp_path / "alerted.json"
    path.write_text("{ this is not json", encoding="utf-8")

    assert AlertLedger(path).claim("k") is True
    assert (tmp_path / "alerted.json.corrupt").read_text(encoding="utf-8") == "{ this is not json"


def test_ledger_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr("navig.agent.proactive.alert_ledger.MAX_KEYS", 5)
    ledger = AlertLedger(tmp_path / "alerted.json")
    for i in range(12):
        ledger.claim(f"k{i}")
    assert len(json.loads((tmp_path / "alerted.json").read_text(encoding="utf-8"))) == 5


# ── gateway auto-start ───────────────────────────────────────────────────────


class _FakeEngine:
    def __init__(self, real):
        self.real = real
        self.running = False
        self.started = 0

    def has_real_provider(self):
        return self.real

    async def start(self):
        self.started += 1


class _CM:
    def __init__(self, value):
        self.value = value

    def get(self, key, default=None):
        return self.value if key == "proactive.enabled" else default


@pytest.mark.parametrize(
    "enabled,real,expect_started",
    [(None, True, 1), ("true", True, 1), ("false", True, 0), (False, True, 0), (None, False, 0)],
)
async def test_auto_start_needs_a_real_provider_and_is_switchable(monkeypatch, enabled, real, expect_started):
    from navig.gateway.server import NavigGateway

    eng = _FakeEngine(real)
    monkeypatch.setattr("navig.agent.proactive.engine.get_proactive_engine", lambda: eng)
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _CM(enabled))

    await NavigGateway._auto_start_proactive(SimpleNamespace())

    assert eng.started == expect_started
