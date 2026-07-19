"""The "🔎 Read <site>" button: web_fetch a result URL on tap and summarize it.

This is the capability the conversational tier lacks — the reason the bot answered
"can't access web directly" when asked to dig into a site. The button delivers it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import navig.tools.web as web
from navig.gateway.channels import telegram_keyboards as kb
from navig.gateway.channels.telegram_keyboards import (
    _WEBFETCH_CB_PREFIX,
    _WEBFETCH_STORE_PREFIX,
    CallbackStore,
    build_read_site_button,
    build_read_site_buttons,
)

pytestmark = pytest.mark.integration


def test_build_read_site_button_stores_url_and_query_and_labels_by_domain():
    store = CallbackStore()
    btn = build_read_site_button(
        "https://www.cybesis.com/about", store=store, query="what games do they make"
    )

    assert btn is not None
    assert btn["callback_data"].startswith(_WEBFETCH_CB_PREFIX)
    assert len(btn["callback_data"].encode()) <= 64  # Telegram limit
    assert "Read" in btn["text"] and "cybesis.com" in btn["text"]  # www. stripped

    key = btn["callback_data"][len(_WEBFETCH_CB_PREFIX):]
    payload = store.get(_WEBFETCH_STORE_PREFIX + key)
    # The user's original question rides along so the on-tap summary answers it.
    assert payload == {"url": "https://www.cybesis.com/about", "query": "what games do they make"}


def test_build_read_site_button_rejects_non_http():
    assert build_read_site_button("") is None
    assert build_read_site_button("cybesis.com") is None  # no scheme
    assert build_read_site_button("ftp://cybesis.com") is None


def test_build_read_site_buttons_dedupes_by_domain_and_caps():
    store = CallbackStore()
    urls = [
        "https://cybesis.com/about",
        "https://cybesis.com/blog",  # same domain → deduped
        "https://en.wikipedia.org/wiki/Cybesis",
        "not-a-url",  # skipped
        "https://example.com/x",
        "https://foo.com/y",  # beyond max_n
    ]
    btns = build_read_site_buttons(urls, max_n=3, store=store)

    assert len(btns) == 3
    # First result keeps the fuller "Read" label; the rest are compact.
    assert btns[0]["text"].startswith("🔎 Read ")
    assert btns[1]["text"].startswith("🔎 ") and "Read" not in btns[1]["text"]
    labels = " ".join(b["text"] for b in btns)
    assert "cybesis.com" in labels and "wikipedia.org" in labels and "example.com" in labels
    assert labels.count("cybesis") == 1  # domain deduped
    assert all(b["callback_data"].startswith(_WEBFETCH_CB_PREFIX) for b in btns)


def test_build_read_site_buttons_empty_when_no_valid_urls():
    assert build_read_site_buttons(["", "ftp://x", "notaurl"]) == []
    assert build_read_site_buttons([]) == []


def test_build_read_site_buttons_carry_sibling_set():
    store = CallbackStore()
    urls = ["https://a.com/x", "https://b.com/y", "https://c.com/z"]
    btns = build_read_site_buttons(urls, max_n=3, store=store, query="q")

    assert len(btns) == 3
    # Every button's payload carries the full deduped set, so the on-tap summary can
    # offer to read the OTHER sources without scrolling back.
    for b in btns:
        key = b["callback_data"][len(_WEBFETCH_CB_PREFIX):]
        payload = store.get(_WEBFETCH_STORE_PREFIX + key)
        assert payload["siblings"] == urls
        assert payload["query"] == "q"


async def test_webfetch_callback_offers_other_sources(monkeypatch):
    """After reading one source, the summary offers buttons to read the OTHER
    sources from the same search (not the one just read)."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.tools.web import WebFetchResult

    channel = TelegramChannel(bot_token="1:FAKE")
    channel._keep_typing = AsyncMock()
    channel._api_call = AsyncMock(return_value={"ok": True})
    channel.on_message = AsyncMock(return_value="Summary.")
    sent: dict = {}

    async def _send(chat_id, text, **kw):
        sent["keyboard"] = kw.get("keyboard")
        return {"message_id": 1}

    channel.send_message = _send
    monkeypatch.setattr(
        web,
        "web_fetch",
        lambda url, **kw: WebFetchResult(success=True, text="body", title="T"),
    )

    handler = kb.CallbackHandler(channel)
    current = "https://a.com/x"
    handler.store.put(
        _WEBFETCH_STORE_PREFIX + "k",
        {"url": current, "query": "q", "siblings": [current, "https://b.com/y", "https://c.com/z"]},
        ttl=60,
    )

    await handler._handle_webfetch_callback(
        cb_id="cb", chat_id=1, user_id=2, url_key=_WEBFETCH_STORE_PREFIX + "k"
    )

    flat = [b for row in (sent["keyboard"] or []) for b in row]
    assert any(b.get("url") == current for b in flat)  # "Open" the current page
    # Read-another buttons for the OTHER sources — not the one just read.
    read_labels = " ".join(
        b["text"] for b in flat if str(b.get("callback_data", "")).startswith(_WEBFETCH_CB_PREFIX)
    )
    assert "b.com" in read_labels and "c.com" in read_labels
    assert "a.com" not in read_labels


async def test_webfetch_callback_fetches_summarizes_and_links(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.tools.web import WebFetchResult

    channel = TelegramChannel(bot_token="1:FAKE")
    channel._keep_typing = AsyncMock()
    channel._api_call = AsyncMock(return_value={"ok": True})
    sent: dict = {}

    async def _send(chat_id, text, **kw):
        sent["text"] = text
        sent["keyboard"] = kw.get("keyboard")
        return {"message_id": 1}

    channel.send_message = _send

    async def _on_message(**kwargs):
        msg = kwargs.get("message", "")
        # The prompt includes the fetched content AND the user's original question,
        # so the summary answers what they actually asked (not a generic blurb).
        assert "page body" in msg
        assert "what games" in msg
        return "SUMMARY: Cybesis makes indie games."

    channel.on_message = _on_message

    monkeypatch.setattr(
        web,
        "web_fetch",
        lambda url, **kw: WebFetchResult(success=True, text="page body here", title="Cybesis"),
    )

    handler = kb.CallbackHandler(channel)
    url = "https://cybesis.com/about"
    handler.store.put(
        _WEBFETCH_STORE_PREFIX + "k12345",
        {"url": url, "query": "what games do they make"},
        ttl=60,
    )

    await handler._handle_webfetch_callback(
        cb_id="cb", chat_id=1, user_id=2, url_key=_WEBFETCH_STORE_PREFIX + "k12345"
    )

    assert "SUMMARY" in sent["text"]
    flat = [b for row in (sent["keyboard"] or []) for b in row]
    assert any(b.get("url") == url for b in flat)  # links out to the real page


async def test_webfetch_callback_accepts_legacy_string_payload(monkeypatch):
    """A button stored before the {url,query} change kept a plain-string URL — the
    callback must still fetch + summarize it (generic, no query)."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.tools.web import WebFetchResult

    channel = TelegramChannel(bot_token="1:FAKE")
    channel._keep_typing = AsyncMock()
    channel._api_call = AsyncMock(return_value={"ok": True})
    sent: dict = {}

    async def _send(chat_id, text, **kw):
        sent["text"] = text
        return {"message_id": 1}

    channel.send_message = _send

    async def _on_message(**kwargs):
        return "Generic page summary."

    channel.on_message = _on_message
    monkeypatch.setattr(
        web,
        "web_fetch",
        lambda url, **kw: WebFetchResult(success=True, text="body", title="X"),
    )

    handler = kb.CallbackHandler(channel)
    handler.store.put(_WEBFETCH_STORE_PREFIX + "legacy", "https://cybesis.com/", ttl=60)

    await handler._handle_webfetch_callback(
        cb_id="cb", chat_id=1, user_id=2, url_key=_WEBFETCH_STORE_PREFIX + "legacy"
    )

    assert "Generic page summary" in sent["text"]


async def test_webfetch_callback_caches_summary_on_retap(monkeypatch):
    """A re-tap of the same (url, query) — common now that the summary offers sibling
    sources — returns the cached summary without re-fetching or re-calling the LLM."""
    from navig.gateway.channels.telegram import TelegramChannel
    from navig.tools.web import WebFetchResult

    channel = TelegramChannel(bot_token="1:FAKE")
    channel._keep_typing = AsyncMock()
    channel._api_call = AsyncMock(return_value={"ok": True})
    channel.send_message = AsyncMock(return_value={"message_id": 1})

    fetch_calls = {"n": 0}

    def _fetch(url, **kw):
        fetch_calls["n"] += 1
        return WebFetchResult(success=True, text="page body", title="T")

    monkeypatch.setattr(web, "web_fetch", _fetch)

    llm_calls = {"n": 0}

    async def _on_message(**kwargs):
        llm_calls["n"] += 1
        return "SUMMARY."

    channel.on_message = _on_message

    handler = kb.CallbackHandler(channel)
    # Unique url+query — the CallbackStore is a process-wide singleton, so a shared
    # value would collide with other tests' cache entries.
    handler.store.put(
        _WEBFETCH_STORE_PREFIX + "cachek",
        {"url": "https://cache-test.example/page", "query": "unique-cache-query"},
        ttl=60,
    )
    kwargs = dict(cb_id="c", chat_id=1, user_id=2, url_key=_WEBFETCH_STORE_PREFIX + "cachek")

    # First tap: fetch + summarize.
    await handler._handle_webfetch_callback(**kwargs)
    assert fetch_calls["n"] == 1 and llm_calls["n"] == 1

    # Second tap of the SAME button: served from cache — no fetch, no LLM.
    await handler._handle_webfetch_callback(**kwargs)
    assert fetch_calls["n"] == 1 and llm_calls["n"] == 1

    # Both taps still posted the summary body.
    assert channel.send_message.await_count == 2
    assert "SUMMARY" in channel.send_message.await_args_list[-1].args[1]


async def test_webfetch_callback_reports_expired_link(monkeypatch):
    from navig.gateway.channels.telegram import TelegramChannel

    channel = TelegramChannel(bot_token="1:FAKE")
    channel._api_call = AsyncMock(return_value={"ok": True})
    channel.send_message = AsyncMock(return_value={"message_id": 1})

    handler = kb.CallbackHandler(channel)
    # Nothing stored under this key → expired.
    await handler._handle_webfetch_callback(
        cb_id="cb", chat_id=1, user_id=2, url_key=_WEBFETCH_STORE_PREFIX + "missing"
    )

    # Answered with an "expired" toast; no message body sent.
    channel._api_call.assert_awaited()
    channel.send_message.assert_not_awaited()
