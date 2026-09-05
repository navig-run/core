"""Stage D — the SMTP send path that `navig email send` now calls (was a stub).

Proves GmailProvider.send_email builds a correct MIME message and drives SMTP
login + send, without a real server (the SSL transport is faked).
"""

from __future__ import annotations

import pytest

import navig.agent.proactive.imap_email as mod
from navig.agent.proactive.imap_email import GmailProvider, IMAPEmailProvider, OutlookProvider

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


class _FakeSMTPStartTLS:
    """Stand-in for smtplib.SMTP (plaintext + STARTTLS) that records the exchange."""

    captured: dict = {}

    def __init__(self, host, port):
        _FakeSMTPStartTLS.captured = {"host": host, "port": port, "starttls": False}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        _FakeSMTPStartTLS.captured["starttls"] = True

    def login(self, user, pw):
        _FakeSMTPStartTLS.captured["login"] = (user, pw)

    def send_message(self, msg):
        _FakeSMTPStartTLS.captured["msg"] = msg


async def test_outlook_send_uses_starttls_on_587(monkeypatch):
    # Outlook/Office 365 submit on 587 (STARTTLS). Using SMTP_SSL there fails the
    # handshake, so send_email must go through plain SMTP + starttls(), never SMTP_SSL.
    monkeypatch.setattr(mod, "SMTP", _FakeSMTPStartTLS, raising=False)

    def _ssl_boom(*a, **k):
        raise AssertionError("SMTP_SSL must not be used for a STARTTLS (587) provider")

    monkeypatch.setattr(mod, "SMTP_SSL", _ssl_boom)

    p = OutlookProvider(email_address="me@outlook.com", password="pw")
    ok = await p.send_email(["bob@x.com"], "Hi", "Body")
    assert ok is True
    c = _FakeSMTPStartTLS.captured
    assert c["host"] == "smtp.office365.com" and c["port"] == 587
    assert c["starttls"] is True  # explicit TLS negotiated before login
    assert c["login"] == ("me@outlook.com", "pw")
