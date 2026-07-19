"""Stage C — Gmail compose recipe (deep-link URL builder + compose/send/not-signed-in logic)."""

from __future__ import annotations

import pytest

from navig.browser.recipes import gmail

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# compose_url — the stable deep-link
# ---------------------------------------------------------------------------


def test_compose_url_basic():
    u = gmail.compose_url("bob@x.com", "Hi there", "Hello world")
    assert u.startswith("https://mail.google.com/mail/u/0/?")
    assert "view=cm" in u and "fs=1" in u
    assert "to=bob%40x.com" in u
    assert "su=Hi%20there" in u
    assert "body=Hello%20world" in u


def test_compose_url_encodes_special_chars():
    u = gmail.compose_url("a@b.com", "50% off & more", "line1\nline2 <tag>")
    assert "su=50%25%20off%20%26%20more" in u
    assert "%0A" in u  # newline encoded
    assert "%3Ctag%3E" in u  # < > encoded


def test_compose_url_cc_bcc_and_index_account():
    u = gmail.compose_url("t@x.com", cc="c@x.com", bcc="b@x.com", account=2)
    assert "/u/2/?" in u
    assert "cc=c%40x.com" in u and "bcc=b%40x.com" in u


def test_compose_url_by_email_uses_authuser():
    u = gmail.compose_url("t@x.com", account="work@company.com")
    assert u.startswith("https://mail.google.com/mail/?")   # email path, not /u/<n>/
    assert "/u/" not in u
    assert "authuser=work%40company.com" in u
    assert "view=cm" in u and "to=t%40x.com" in u


def test_compose_url_empty_is_blank_compose():
    u = gmail.compose_url()
    assert u.endswith("?view=cm&fs=1")


# ---------------------------------------------------------------------------
# compose() — signed-in / send / not-signed-in, with a fake controller
# ---------------------------------------------------------------------------


class _FakeKeyboard:
    def __init__(self):
        self.pressed = []

    async def press(self, combo):
        self.pressed.append(combo)


class _FakePage:
    def __init__(self, send_button=True):
        self.keyboard = _FakeKeyboard()
        self.clicks = []
        self._send_button = send_button

    async def click(self, selector, **kwargs):
        # Simulate the Send button being absent → force the Ctrl+Enter fallback.
        if not self._send_button and "Send" in selector:
            raise RuntimeError("no send button")
        self.clicks.append(selector)


class _FakeController:
    def __init__(self, final_url, *, compose_ready=True, sent_confirms=True, send_button=True):
        self._final = final_url
        self._page = _FakePage(send_button=send_button)
        self.navigated = None
        self._compose_ready = compose_ready
        self._sent_confirms = sent_confirms

    async def navigate(self, url):
        self.navigated = url

    async def wait_for_stable(self):
        return None

    async def get_url(self):
        return self._final

    async def wait_for_selector(self, selector, timeout=5000, state="visible"):
        if "Message Body" in selector:
            return self._compose_ready              # render-readiness check
        if "link_undo" in selector or "Message sent" in selector:
            return self._sent_confirms              # "Message sent" toast appeared
        if state == "hidden":
            return self._sent_confirms              # compose window closed
        return not self._sent_confirms


async def test_compose_only_does_not_send():
    c = _FakeController("https://mail.google.com/mail/u/0/?view=cm&fs=1&to=bob%40x.com")
    r = await gmail.compose(c, to="bob@x.com", subject="Hi", body="yo", send=False)
    assert r["ok"] and r["status"] == "composed" and r["sent"] is False
    assert c._page.keyboard.pressed == []  # nothing sent
    assert "view=cm" in c.navigated


async def test_compose_send_confirmed_via_send_button():
    c = _FakeController("https://mail.google.com/mail/u/0/?view=cm&fs=1", sent_confirms=True)
    r = await gmail.compose(c, to="bob@x.com", body="hi", send=True)
    assert r["status"] == "sent" and r["sent"] is True
    assert any("Send" in s for s in c._page.clicks)  # clicked the Send button


async def test_compose_send_falls_back_to_ctrl_enter_when_no_send_button():
    c = _FakeController("https://mail.google.com/mail/u/0/?view=cm&fs=1",
                        sent_confirms=True, send_button=False)
    r = await gmail.compose(c, to="bob@x.com", body="hi", send=True)
    assert r["status"] == "sent" and r["sent"] is True
    assert c._page.keyboard.pressed == ["Control+Enter"]


async def test_compose_send_unconfirmed_when_compose_stays_open():
    # send action fired but the compose window never closed → do NOT claim sent
    c = _FakeController("https://mail.google.com/mail/u/0/?view=cm&fs=1", sent_confirms=False)
    r = await gmail.compose(c, to="bob@x.com", body="hi", send=True)
    assert r["status"] == "send_unconfirmed" and r["sent"] is False
    assert r["ok"] is False  # so --json exit code matches the human path (both fail)


async def test_compose_detects_not_signed_in():
    c = _FakeController("https://accounts.google.com/signin/v2/identifier?service=mail")
    r = await gmail.compose(c, to="bob@x.com", send=True)
    assert not r["ok"] and r["status"] == "not_signed_in"
    assert c._page.keyboard.pressed == []  # never tries to send when not logged in


async def test_compose_does_not_send_when_window_not_ready():
    # signed in, but the compose form never rendered → don't blind-send, and since a
    # send was requested, report it as unconfirmed (ok=False), not a false success.
    c = _FakeController("https://mail.google.com/mail/u/0/?view=cm&fs=1", compose_ready=False)
    r = await gmail.compose(c, to="bob@x.com", body="hi", send=True)
    assert r["status"] == "send_unconfirmed" and r["sent"] is False and r["ok"] is False
    assert c._page.keyboard.pressed == []  # never pressed Ctrl+Enter blindly
