"""Listing unread mail must NEVER mark it read on the user's real server.

`IMAPEmailProvider.list_unread` opened INBOX read-write and fetched with ``RFC822``
(== ``BODY[]``), which sets the ``\\Seen`` flag per RFC 3501 — so the proactive engine's
~60s poll silently marked the user's genuinely-unread emails as read, irreversibly. Now
it opens the mailbox read-only and fetches with ``BODY.PEEK[]``. Under the old code the
first two tests fail (select without readonly / fetch with RFC822).
"""

from __future__ import annotations

import pytest

from navig.agent.proactive import imap_email
from navig.agent.proactive.imap_email import IMAPEmailProvider

pytestmark = pytest.mark.asyncio

_RAW = (
    b"From: alice@example.com\r\n"
    b"Subject: Hi there\r\n"
    b"Date: Wed, 23 Jul 2026 10:00:00 +0000\r\n"
    b"\r\n"
    b"body text\r\n"
)


class _FakeIMAP:
    def __init__(self):
        self.select_calls: list = []
        self.fetch_specs: list = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        pass

    def select(self, mailbox, readonly=False):
        self.select_calls.append((mailbox, readonly))
        return ("OK", [b"2"])

    def search(self, charset, criterion):
        return ("OK", [b"1 2"])  # two UNSEEN message ids

    def fetch(self, msg_id, spec):
        self.fetch_specs.append(spec)
        return ("OK", [(b"X (BODY[] {%d}" % len(_RAW), _RAW), b")"])


@pytest.fixture
def fake_imap(monkeypatch):
    fake = _FakeIMAP()
    monkeypatch.setattr(imap_email, "IMAP4_SSL", lambda *a, **k: fake)
    return fake


def _provider():
    return IMAPEmailProvider(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        email_address="me@example.com",
        password="secret",
    )


async def test_list_unread_opens_mailbox_read_only(fake_imap):
    await _provider().list_unread(limit=5)
    assert fake_imap.select_calls == [("INBOX", True)]


async def test_list_unread_uses_body_peek_never_rfc822(fake_imap):
    await _provider().list_unread(limit=5)
    assert fake_imap.fetch_specs  # messages were actually fetched
    assert all(spec == "(BODY.PEEK[])" for spec in fake_imap.fetch_specs)
    assert "(RFC822)" not in fake_imap.fetch_specs


async def test_list_unread_still_parses_messages(fake_imap):
    msgs = await _provider().list_unread(limit=5)
    assert len(msgs) == 2
    assert msgs[0].subject == "Hi there"
    assert msgs[0].sender == "alice@example.com"
    assert msgs[0].read is False
