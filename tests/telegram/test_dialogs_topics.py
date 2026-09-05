"""`dialogs.list_topics` — the forum-topic lister, which was dead on any current install.

Telegram moved the forum-topic methods from the ``channels`` namespace to ``messages``
and telethon followed in 1.44, so the module-level import raised at call time:

    ImportError: cannot import name 'GetForumTopicsRequest' from
    'telethon.tl.functions.channels'

The single `limit=100` request was a second, quieter bug: a forum with more topics than
that returned a truncated list with no indication, which is the worst possible failure
for "archive this group before deleting it".
"""

from __future__ import annotations

import datetime as dt

import pytest

from navig.telegram import dialogs


class _Topic:
    def __init__(self, tid, title, top_message, pinned=False, closed=False):
        self.id = tid
        self.title = title
        self.top_message = top_message
        self.pinned = pinned
        self.closed = closed
        self.icon_color = 0x6FB9F0


class _Msg:
    def __init__(self, mid, date):
        self.id = mid
        self.date = date


class _Res:
    def __init__(self, topics, messages):
        self.topics = topics
        self.messages = messages


class _Client:
    """Serves topics in pages, recording each request so pagination can be asserted."""

    def __init__(self, total, page=100):
        self.all = [_Topic(i, f"topic {i}", 1000 + i) for i in range(1, total + 1)]
        self.page = page
        self.requests: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get_entity(self, ref):
        return object()

    async def get_dialogs(self):
        return []

    async def __call__(self, req):
        self.requests.append(req)
        start = 0
        if req.offset_topic:
            start = next((i for i, t in enumerate(self.all)
                          if t.id == req.offset_topic), -1) + 1
        chunk = self.all[start:start + min(req.limit, self.page)]
        msgs = [_Msg(t.top_message, dt.datetime(2026, 1, 1)) for t in chunk]
        return _Res(chunk, msgs)


def _patch(monkeypatch, client):
    monkeypatch.setattr(dialogs, "UserClient", lambda: client)
    return client


def test_the_request_class_resolves_on_this_telethon():
    """The regression itself: the import must not raise on a current telethon."""
    req = dialogs._get_forum_topics_request()
    assert req.__name__ == "GetForumTopicsRequest"


def test_request_is_built_with_peer_not_channel(monkeypatch):
    """The new signature takes `peer=`; `channel=` is a TypeError on telethon >= 1.44."""
    c = _patch(monkeypatch, _Client(3))
    pytest.importorskip("telethon")
    rows = dialogs.list_topics
    import asyncio
    asyncio.run(rows("-1001736302429"))
    assert c.requests, "no request was issued"
    assert hasattr(c.requests[0], "peer")
    assert not hasattr(c.requests[0], "channel")


@pytest.mark.asyncio
async def test_returns_every_topic_across_pages(monkeypatch):
    """250 topics must come back as 250, not silently clipped to the first page."""
    c = _patch(monkeypatch, _Client(250, page=100))
    rows = await dialogs.list_topics("-1001736302429", page=100)
    assert len(rows) == 250
    assert [r["topic_id"] for r in rows] == list(range(1, 251))
    assert len(c.requests) >= 3          # actually paged, not one big request


@pytest.mark.asyncio
async def test_single_page_forum_issues_no_extra_request(monkeypatch):
    c = _patch(monkeypatch, _Client(7, page=100))
    rows = await dialogs.list_topics("x")
    assert len(rows) == 7
    assert len(c.requests) == 1          # short page ⇒ stop, don't ask again


@pytest.mark.asyncio
async def test_limit_caps_the_result_and_stops_paging(monkeypatch):
    c = _patch(monkeypatch, _Client(250, page=100))
    rows = await dialogs.list_topics("x", limit=120, page=100)
    assert len(rows) == 120
    assert len(c.requests) == 2


@pytest.mark.asyncio
async def test_empty_forum_returns_empty_list(monkeypatch):
    _patch(monkeypatch, _Client(0))
    assert await dialogs.list_topics("x") == []


@pytest.mark.asyncio
async def test_rows_carry_title_flags_and_top_message(monkeypatch):
    c = _Client(0)
    c.all = [_Topic(2, "General", 900, pinned=True), _Topic(78, "drops", 993, closed=True)]
    _patch(monkeypatch, c)
    rows = await dialogs.list_topics("x")
    assert rows[0] == {"topic_id": 2, "title": "General", "icon_color": 0x6FB9F0,
                       "closed": False, "pinned": True, "top_message": 900}
    assert rows[1]["closed"] is True


@pytest.mark.asyncio
async def test_stops_instead_of_spinning_when_the_offset_stops_advancing(monkeypatch):
    """A server that keeps returning the same page must not loop forever."""

    class _Stuck(_Client):
        async def __call__(self, req):
            self.requests.append(req)
            if len(self.requests) > 10:
                raise AssertionError("looped instead of breaking on no progress")
            t = _Topic(5, "same", 50)
            return _Res([t], [_Msg(50, dt.datetime(2026, 1, 1))])

    c = _patch(monkeypatch, _Stuck(0))
    rows = await dialogs.list_topics("x", page=1)
    assert len(rows) == 1
    assert len(c.requests) <= 2
