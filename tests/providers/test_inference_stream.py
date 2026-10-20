"""
astream_via_connection — the streaming bridge the NAVIG Dock side panel reads.

Proves it is a REAL stream (a delta reaches the caller before the provider has
finished), that it rotates to a sibling account only before the first delta, and
that a pinned connection never rotates. No network: the account client is faked.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.providers import inference as inf


def _conn(cid, name, *, default=False, routable=True):
    return {
        "connection_id": cid, "name": name, "template_id": "openai-api", "driver": "native",
        "is_default": default, "is_routable": routable, "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini"], "metadata": {},
    }


class _Chunk:
    def __init__(self, delta):
        self.delta = delta


class _FakeClient:
    def __init__(self, script):
        self.script = script  # list of str deltas, or Exception to raise at that point
        self.closed = False
        self.requests = []

    async def complete_stream(self, request):
        self.requests.append(request)
        for item in self.script:
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, asyncio.Event):
                await item.wait()
                continue
            yield _Chunk(item)

    async def close(self):
        self.closed = True


class _RateLimited(Exception):
    status_code = 429


@pytest.fixture(autouse=True)
def _fresh_cooldowns(monkeypatch):
    from navig.llm import fallback_policy

    monkeypatch.setattr(fallback_policy, "_cooldowns", {})


def _setup(monkeypatch, conns, scripts):
    monkeypatch.setattr("navig.providers.connect.list_connections", lambda *a, **k: conns)
    monkeypatch.setattr(inf, "list_provider_connections", lambda pid: list(conns))
    clients: dict[str, _FakeClient] = {}

    async def fake_account_client(conn, provider_id, timeout):
        c = _FakeClient(scripts[conn["connection_id"]])
        clients[conn["connection_id"]] = c
        return c

    monkeypatch.setattr(inf, "_account_client", fake_account_client)
    return clients


async def _collect(**kw):
    out = []
    async for d in inf.astream_via_connection(messages=[{"role": "user", "content": "hi"}], **kw):
        out.append(d)
    return out


async def test_yields_before_the_provider_finishes(monkeypatch):
    gate = asyncio.Event()
    clients = _setup(monkeypatch, [_conn("a", "A", default=True)], {"a": ["Hel", gate, "lo"]})
    gen = inf.astream_via_connection(messages=[{"role": "user", "content": "hi"}])
    first = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert first == "Hel"          # arrived while the provider is still blocked on `gate`
    gate.set()
    assert [d async for d in gen] == ["lo"]
    assert clients["a"].closed is True
    req = clients["a"].requests[0]
    assert req.stream is True and req.model == "gpt-4o-mini"


async def test_rotates_to_sibling_before_first_delta(monkeypatch):
    conns = [_conn("a", "A", default=True), _conn("b", "B")]
    seen = []
    _setup(monkeypatch, conns, {"a": [_RateLimited("429 rate limited")], "b": ["ok"]})
    assert await _collect(on_account=seen.append) == ["ok"]
    assert [s["name"] for s in seen] == ["A", "B"]  # surface can label who answered


async def test_never_restarts_after_text_reached_the_caller(monkeypatch):
    conns = [_conn("a", "A", default=True), _conn("b", "B")]
    clients = _setup(monkeypatch, conns, {"a": ["partial", _RateLimited("429")], "b": ["dup"]})
    with pytest.raises(_RateLimited):
        await _collect()
    assert "b" not in clients  # a sibling would have repeated the answer from scratch


async def test_pinned_connection_does_not_rotate(monkeypatch):
    conns = [_conn("a", "A", default=True), _conn("b", "B")]
    clients = _setup(monkeypatch, conns, {"a": [_RateLimited("429")], "b": ["ok"]})
    with pytest.raises(_RateLimited):
        await _collect(connection_id="a")
    assert "b" not in clients


async def test_no_connection_is_an_honest_error(monkeypatch):
    monkeypatch.setattr("navig.providers.connect.list_connections", lambda *a, **k: [])
    with pytest.raises(inf.NoRoutableConnection):
        await _collect()


async def test_external_runtime_is_refused(monkeypatch):
    ext = _conn("x", "Codex", default=True)
    ext["template_id"] = "codex"
    monkeypatch.setattr("navig.providers.connect.list_connections", lambda *a, **k: [ext])
    with pytest.raises(inf.ExternalNotRoutable):
        await _collect()
