"""Provider clients share keep-alive connections, never credentials.

The agent loop builds and closes a provider client every turn, so each reply used to
open a fresh TCP + TLS connection (measured ~407 ms vs ~128 ms on a kept-alive one).
Clients now draw sockets from one transport per main-thread event loop. These tests
pin what makes that safe: each client still sends ITS OWN credential, closing a
client never tears the pool down, and nothing is shared across threads or loops.
"""

from __future__ import annotations

import asyncio
import threading
import weakref

import httpx
import pytest

from navig.providers import clients
from navig.providers.clients import OpenAIClient, get_builtin_provider


class _RecordingTransport(httpx.MockTransport):
    """In-memory pool stand-in that, like a real pool, is unusable once closed."""

    def __init__(self, handler) -> None:  # noqa: ANN001
        super().__init__(handler)
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.closed:
            raise RuntimeError("pooled transport used after it was closed")
        return await super().handle_async_request(request)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    """Route the shared pool to an in-memory transport that records each request."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(clients, "_SHARED_TRANSPORTS", weakref.WeakKeyDictionary())
    monkeypatch.setattr(clients, "_make_pooled_transport", lambda: _RecordingTransport(handler))
    return seen


def _client(api_key: str) -> OpenAIClient:
    return OpenAIClient(get_builtin_provider("openai"), api_key=api_key)


async def test_clients_on_one_loop_share_the_pool(recorded):
    a, b = _client("key-a"), _client("key-b")
    ca, cb = await a._get_client(), await b._get_client()
    assert ca is not cb, "each provider client keeps its own AsyncClient"
    assert ca._transport is cb._transport, "but they draw from one connection pool"
    assert isinstance(ca._transport, clients._NonClosingTransport)


async def test_each_client_still_sends_its_own_credential(recorded):
    """The safety property: a shared pool must never mix up whose key is sent."""
    a, b = _client("key-a"), _client("key-b")
    await (await a._get_client()).get("https://api.example.test/v1/x")
    await (await b._get_client()).get("https://api.example.test/v1/x")
    await (await a._get_client()).get("https://api.example.test/v1/x")
    assert [r.headers["authorization"] for r in recorded] == [
        "Bearer key-a",
        "Bearer key-b",
        "Bearer key-a",
    ]


async def test_closing_a_client_keeps_the_pool_for_the_next_turn(recorded):
    first = _client("key-a")
    pool = (await first._get_client())._transport
    await first.close()  # what run_agentic does at the end of every turn
    nxt = _client("key-a")
    c = await nxt._get_client()
    assert c._transport is pool, "the next turn must reuse the warm pool"
    assert (await c.get("https://api.example.test/v1/x")).status_code == 200


def test_worker_threads_get_a_private_transport(recorded):
    """Short-lived loops in worker threads must not share (or poison) the pool."""
    result: dict[str, object] = {}

    def run() -> None:
        async def inner() -> None:
            result["shared"] = clients._shared_transport()
            c = await _client("key-a")._get_client()
            result["is_pooled"] = isinstance(c._transport, clients._NonClosingTransport)
            await c.aclose()

        asyncio.run(inner())

    t = threading.Thread(target=run)
    t.start()
    t.join(timeout=30)
    assert result == {"shared": None, "is_pooled": False}


def test_each_event_loop_gets_its_own_pool(recorded):
    async def pool() -> object:
        return clients._shared_transport()

    first = asyncio.run(pool())
    second = asyncio.run(pool())
    assert first is not None and second is not None
    assert first is not second, "connections are bound to the loop that opened them"


def test_no_running_loop_means_no_shared_pool(recorded):
    assert clients._shared_transport() is None
