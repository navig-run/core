"""Tests for the Studio social scheduler (Part 2).

Covers the scheduled-posts store, PostContent rendering, the PublishDispatcher
fan-out, the requires_auth gating, the ScheduledPostService, and the Studio
deck route handlers. Isolated via the session config fixture + a per-test store.
"""

from __future__ import annotations

import json

import pytest

import navig.store.scheduled_posts as sp
from navig.store.scheduled_posts import ScheduledPostStore
from navig.social.types import PostContent, PublishReceipt


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = ScheduledPostStore(db_path=tmp_path / "posts.db")
    monkeypatch.setattr(sp, "_store", s)
    return s


# ── Store ─────────────────────────────────────────────────


def test_store_crud_and_due(store):
    pid = store.create(body="hi", content={"body": "hi"}, targets=[{"network": "twitter", "target": ""}],
                       status="scheduled", schedule_kind="once", run_at="2020-01-01T00:00:00Z")
    store.create(body="future", status="scheduled", schedule_kind="once", run_at="2999-01-01T00:00:00Z")
    store.create(body="draft", status="draft")

    got = store.get(pid)
    assert got["body"] == "hi" and got["targets"][0]["network"] == "twitter"

    due = store.due("2026-06-19T12:00:00Z")
    assert [p["id"] for p in due] == [pid]  # only the past-dated scheduled post

    assert store.update(pid, status="published", receipts=[{"network": "twitter", "ok": True}])
    assert store.get(pid)["status"] == "published"
    assert store.get(pid)["receipts"][0]["ok"] is True

    assert len(store.list(status="draft")) == 1
    assert store.delete(pid) is True
    assert store.get(pid) is None


# ── PostContent rendering ─────────────────────────────────


def test_render_hashtags_link_and_override():
    pc = PostContent(body="Hello", hashtags=["navig", "#ai"], link="https://x.io",
                     per_network={"twitter": {"body": "Hi X"}})
    default = pc.render("discord")
    assert "Hello" in default.text and "#navig" in default.text and "#ai" in default.text
    assert "https://x.io" in default.text
    assert pc.render("twitter").text.startswith("Hi X")

    rt = PostContent.from_dict(pc.to_dict())
    assert rt.render("twitter").text == pc.render("twitter").text


# ── Dispatcher ────────────────────────────────────────────


class FakeTG:
    _session = None

    async def send_message(self, chat_id, text, **k):
        return {"message_id": 123}


class _FakeReceipt:
    def __init__(self, ok, mid=None, err=None):
        self.ok = ok
        self.message_id = mid
        self.error = err


class FakeAdapter:
    async def send_message(self, thread_id, text, attachments=None):
        return _FakeReceipt(True, "disc-1")


class FakeAdapterReg:
    def get(self, name):
        return FakeAdapter() if name == "discord" else None


class FakePublisher:
    name = "twitter"

    async def publish(self, target, post):
        return PublishReceipt.success("twitter", target, id="tw-1")


class FakePubReg:
    def get(self, name):
        return FakePublisher() if name == "twitter" else None


async def test_dispatcher_fans_out():
    from navig.social.dispatcher import PublishDispatcher

    disp = PublishDispatcher(
        telegram_channel=FakeTG(),
        adapter_registry=FakeAdapterReg(),
        publisher_registry=FakePubReg(),
    )
    content = PostContent(body="Broadcast this")
    receipts = await disp.publish(content, [
        {"network": "telegram", "target": "-100"},
        {"network": "discord", "target": "555"},
        {"network": "twitter", "target": ""},
        {"network": "bogus", "target": ""},
    ])
    by_net = {r.network: r for r in receipts}
    assert by_net["telegram"].ok and by_net["telegram"].id == "123"
    assert by_net["discord"].ok and by_net["discord"].id == "disc-1"
    assert by_net["twitter"].ok and by_net["twitter"].id == "tw-1"
    assert not by_net["bogus"].ok


async def test_publisher_requires_auth(monkeypatch):
    # Ensure no token is resolvable.
    monkeypatch.delenv("TWITTER_TOKEN", raising=False)
    monkeypatch.delenv("NAVIG_TWITTER_TOKEN", raising=False)
    from navig.social.publishers import TwitterPublisher

    pub = TwitterPublisher()
    monkeypatch.setattr(pub, "token", lambda: None)
    r = await pub.publish("", PostContent(body="hi").render("twitter"))
    assert not r.ok and r.requires_auth


# ── Scheduler service ─────────────────────────────────────


class FakeGateway:
    def __init__(self):
        self.channels = {"telegram": FakeTG()}


async def test_service_publishes_due(store):
    from navig.social.scheduler_service import ScheduledPostService

    pid = store.create(body="go", content={"body": "go"}, targets=[{"network": "telegram", "target": "-100"}],
                       status="scheduled", schedule_kind="once", run_at="2020-01-01T00:00:00Z")
    svc = ScheduledPostService(FakeGateway())
    n = await svc.tick()
    assert n == 1
    p = store.get(pid)
    assert p["status"] == "published"
    assert p["receipts"][0]["ok"] is True


async def test_service_reschedules_recurring(store):
    from navig.social.scheduler_service import ScheduledPostService

    pid = store.create(body="daily", content={"body": "daily"}, targets=[{"network": "telegram", "target": "-1"}],
                       status="scheduled", schedule_kind="recurring", cron_expr="every 1 hours",
                       run_at="2020-01-01T00:00:00Z")
    svc = ScheduledPostService(FakeGateway())
    await svc.run_post(store, store.get(pid))
    p = store.get(pid)
    assert p["status"] == "scheduled"  # rescheduled, not terminal
    assert p["run_at"] > "2025-01-01T00:00:00Z"  # bumped into the future
    assert p["receipts"][0]["ok"] is True


# ── Routes ────────────────────────────────────────────────


class FakeRequest:
    def __init__(self, match=None, query=None, body=None):
        self.match_info = match or {}
        self.query = query or {}
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _payload(resp):
    return json.loads(resp.body)


async def test_route_networks(store):
    from navig.gateway.deck.routes import studio as st

    resp = await st.handle_studio_networks(FakeRequest())
    nets = _payload(resp)["data"]["networks"]
    names = {n["network"] for n in nets}
    assert {"telegram", "discord", "twitter", "reddit"} <= names
    groups = {n["network"]: n["group"] for n in nets}
    assert groups["telegram"] == "messaging" and groups["twitter"] == "publishing"


async def test_route_post_lifecycle(store):
    from navig.gateway.deck.routes import studio as st

    c = await st.handle_studio_post_create(FakeRequest(body={
        "body": "draft post", "targets": [{"network": "twitter", "target": ""}], "status": "draft",
    }))
    assert c.status == 201
    pid = _payload(c)["data"]["post"]["id"]

    g = await st.handle_studio_post_get(FakeRequest(match={"id": str(pid)}))
    assert _payload(g)["data"]["post"]["body"] == "draft post"

    u = await st.handle_studio_post_update(FakeRequest(match={"id": str(pid)}, body={"body": "edited"}))
    assert _payload(u)["data"]["post"]["body"] == "edited"

    lst = await st.handle_studio_posts_list(FakeRequest(query={"status": "draft"}))
    assert len(_payload(lst)["data"]["posts"]) == 1

    d = await st.handle_studio_post_delete(FakeRequest(match={"id": str(pid)}))
    assert _payload(d)["data"]["deleted"] is True


async def test_route_ai_requires_text(store):
    from navig.gateway.deck.routes import studio as st

    resp = await st.handle_studio_ai(FakeRequest(body={"action": "rewrite"}))
    assert resp.status == 400
