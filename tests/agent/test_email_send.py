"""Stage D — the SMTP send path that `navig email send` now calls (was a stub).

Proves GmailProvider.send_email builds a correct MIME message and drives SMTP
login + send, without a real server (the SSL transport is faked).
"""

from __future__ import annotations

import pytest

import navig.agent.proactive.imap_email as mod
from navig.agent.proactive.imap_email import GmailProvider, IMAPEmailProvider

pytestmark = pytest.mark.integration


class _FakeSMTP:
    """Stand-in for smtplib.SMTP_SSL that records the exchange."""

    captured: dict = {}

    def __init__(self, host, port):
        _FakeSMTP.captured = {"host": host, "port": port}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        _FakeSMTP.captured["login"] = (user, pw)

    def send_message(self, msg):
        _FakeSMTP.captured["msg"] = msg


async def test_gmail_send_builds_and_sends(monkeypatch):
    monkeypatch.setattr(mod, "SMTP_SSL", _FakeSMTP)
    p = GmailProvider(email_address="me@gmail.com", app_password="app-pass")
    ok = await p.send_email(["bob@x.com", "carol@x.com"], "Hello", "Body text")
    assert ok is True
    c = _FakeSMTP.captured
    assert c["host"] == "smtp.gmail.com" and c["port"] == 465
    assert c["login"] == ("me@gmail.com", "app-pass")
    assert c["msg"]["To"] == "bob@x.com, carol@x.com"
    assert c["msg"]["Subject"] == "Hello"
    assert c["msg"]["From"] == "me@gmail.com"


async def test_send_partial_recipient_refusal_surfaces(monkeypatch):
    class _RefusingSMTP(_FakeSMTP):
        def send_message(self, msg):
            _FakeSMTP.captured["msg"] = msg
            return {"bad@invalid": (550, b"no such user")}  # non-empty = partial refusal

    monkeypatch.setattr(mod, "SMTP_SSL", _RefusingSMTP)
    p = GmailProvider(email_address="me@gmail.com", app_password="x")
    with pytest.raises(Exception):  # must NOT silently report success
        await p.send_email(["good@x.com", "bad@invalid"], "s", "b")


async def test_imap_send_uses_configured_host(monkeypatch):
    monkeypatch.setattr(mod, "SMTP_SSL", _FakeSMTP)
    p = IMAPEmailProvider(email_address="u@corp.com", password="pw",
                          imap_host="imap.corp.com", smtp_host="smtp.corp.com",
                          smtp_port=465)
    ok = await p.send_email(["x@corp.com"], "Sub", "Body")
    assert ok is True
    assert _FakeSMTP.captured["host"] == "smtp.corp.com"
