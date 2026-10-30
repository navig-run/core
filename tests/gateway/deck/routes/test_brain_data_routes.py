"""The Brain's data surface for linked clients.

Two independent guards are tested here because they fail differently and either one failing
is a data-integrity bug, not an inconvenience:

* a phone or a stranger must not read the bank (403);
* a *client* install must not answer as if it were the authority (409 ``not_authority``) —
  otherwise two linked machines can bounce a call between them and one answers from a copy.

The success paths run the real MCP tool implementations, so a change to those semantics shows
up here rather than only in the Brain's client.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

DEVICE = "83d011d16b16"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    data = tmp_path / "data"
    data.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("brain:\n  role: brain\n", encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    # No link on this pretend Brain: it is the authority, it does not call itself.
    monkeypatch.setenv("NAVIG_BRAIN_LINK_FILE", str(tmp_path / "brain-link.json"))
    monkeypatch.delenv("NAVIG_BRAIN_ROLE", raising=False)
    return cfg


def _set_role(cfg: Path, role: str) -> None:
    (cfg / "config.yaml").write_text(f"brain:\n  role: {role}\n", encoding="utf-8")


def _make_app(*, device: str | None = DEVICE):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import brain_data

    @web.middleware
    async def _fake_auth(request, handler):
        # The real middleware validates the bearer against the OS device store; these tests
        # are about what the route does with a caller that already passed it. Whether a
        # request looks "local" is decided per test by patching the real predicate, so no
        # test here depends on where the test runner happens to sit.
        if device:
            request["deck_brain_device"] = device
        return await handler(request)

    app = web.Application(middlewares=[_fake_auth])
    brain_data.register(app)
    return app


@pytest.fixture()
def local_desktop(monkeypatch: pytest.MonkeyPatch):
    """Take control of the desktop bypass instead of letting loopback decide it."""

    def _set(value: bool) -> None:
        monkeypatch.setattr("navig.gateway.deck.auth.trusted_local_desktop", lambda request: value)

    return _set


@pytest.fixture()
async def client_factory(local_desktop):
    from aiohttp.test_utils import TestClient, TestServer

    local_desktop(False)  # default: a stranger's machine, no local-desktop exception
    clients = []

    async def _factory(**kwargs):
        app = _make_app(**kwargs)
        client = TestClient(TestServer(app))
        await client.start_server()
        clients.append(client)
        return client

    yield _factory
    for client in clients:
        await client.close()


async def _post(client, path: str, body: dict | None = None):
    response = await client.post(path, json=body if body is not None else {})
    return response.status, await response.json()


# ── who may call ──────────────────────────────────────────────


async def test_a_caller_without_a_brain_device_is_forbidden(client_factory):
    client = await client_factory(device=None)
    status, body = await _post(client, "/api/deck/brain/data/memory/stats")
    assert status == 403
    assert body["ok"] is False
    assert body["code"] == "forbidden"


async def test_a_copied_deck_api_key_cannot_reach_brain_data(monkeypatch, _isolated):
    """The deck key identifies the tenant; it must not become a credential for the bank.

    Every surface shares ``deck.api_key``, so it is copyable by design. Brain data is the one
    surface that refuses it: only a per-machine brain-link device token (or the desktop
    talking to its own daemon) is accepted, which is why a copied key plus a claimed "client"
    role cannot read or rewrite the owner's facts. This runs the REAL middleware, so the
    refusal is the whole deck auth path, not a stub.
    """
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck import auth as deck_auth
    from navig.gateway.deck.routes import brain_data

    monkeypatch.setattr(deck_auth, "trusted_local_desktop", lambda request: False)
    monkeypatch.setattr(deck_auth, "_local_desktop_bypass", lambda request: False)
    monkeypatch.setitem(deck_auth._deck_config, "api_key", "copied-deck-key")
    monkeypatch.setitem(deck_auth._deck_config, "telegram_only", False)
    monkeypatch.setitem(deck_auth._deck_config, "dev_mode", False)

    app = web.Application(middlewares=[deck_auth.deck_auth_middleware])
    brain_data.register(app)

    async with TestClient(TestServer(app)) as client:
        response = await client.post(
            "/api/deck/brain/data/memory/stats",
            headers={
                "Authorization": "Bearer copied-deck-key",
                "Origin": "https://elsewhere.example",
            },
            json={"args": {}},
        )
        assert response.status == 403
        body = await response.json()
        assert body["code"] == "forbidden"
        assert body["error"] == "brain data is served to linked machines and the local desktop only"


async def test_the_local_desktop_may_read_its_own_bank(client_factory, local_desktop):
    local_desktop(True)
    client = await client_factory(device=None)
    status, body = await _post(client, "/api/deck/brain/data/memory/stats")
    assert status == 200
    assert body["ok"] is True


# ── whether this host is the authority ───────────────────────


@pytest.mark.parametrize("role", ["client", "executor"])
async def test_a_non_authority_host_refuses_with_not_authority(client_factory, _isolated, role):
    _set_role(_isolated, role)
    client = await client_factory()
    status, body = await _post(
        client,
        "/api/deck/brain/data/memory/remember",
        {"fact": "a row this client must not own", "source": "test"},
    )
    assert status == 409
    assert body["code"] == "not_authority"
    assert body["role"] == role


async def test_a_brain_host_serves_memory_round_trip(client_factory, _isolated):
    client = await client_factory()
    status, stored = await _post(
        client,
        "/api/deck/brain/data/memory/remember",
        {"fact": "the outpost brain owns this row", "source": "test"},
    )
    assert (status, stored["ok"]) == (200, True)

    status, found = await _post(
        client, "/api/deck/brain/data/memory/retrieve", {"query": "outpost brain", "limit": 5}
    )
    assert (status, found["ok"]) == (200, True)
    assert any("outpost brain" in row.get("content", "") for row in found["data"])

    status, stats = await _post(client, "/api/deck/brain/data/memory/stats")
    assert status == 200 and stats["ok"] is True


async def test_role_is_read_per_request_not_cached(client_factory, _isolated):
    """A promoted machine must not keep answering 409 until something restarts."""
    client = await client_factory()
    _set_role(_isolated, "client")
    status, _ = await _post(client, "/api/deck/brain/data/memory/stats")
    assert status == 409

    _set_role(_isolated, "brain")
    status, body = await _post(client, "/api/deck/brain/data/memory/stats")
    assert (status, body["ok"]) == (200, True)


# ── the surface stays small ──────────────────────────────────


async def test_unknown_memory_operation_is_404(client_factory):
    client = await client_factory()
    status, body = await _post(client, "/api/deck/brain/data/memory/explode")
    assert status == 404
    assert body["code"] == "unknown_op"


async def test_bank_ingest_stores_the_rows_and_dedupes_a_replay(client_factory):
    """The client captures its own transcripts and sends rows; the Brain banks them once."""
    client = await client_factory()
    batch = {
        "machine": "neuron-1",
        "sources": [
            {
                "source_id": "claude_code:neuron-1:session-a",
                "adapter": "claude_code",
                "path": "/home/neuron/.claude/session-a.jsonl",
                "mtime": 1700000000.0,
                "cursor": "42",
            }
        ],
        "turns": {
            "claude_code:neuron-1:session-a": [
                {"msg_id": "m1", "speaker": "user_typed", "text": "the row the Brain must keep"},
                {"msg_id": "m2", "speaker": "assistant", "text": "an answer"},
            ]
        },
    }

    status, body = await _post(client, "/api/deck/brain/data/recall/ingest", batch)
    assert status == 200, body
    assert body["ok"] is True
    assert body["data"] == {"sources": 1, "turns_added": 2, "machine": "neuron-1"}

    # A retry, or the next ingest of the same source, must add nothing twice.
    status, again = await _post(client, "/api/deck/brain/data/recall/ingest", batch)
    assert status == 200, again
    assert again["data"]["turns_added"] == 0

    # And the rows are readable on the Brain, with their provenance intact.
    from navig.recall.store import get_recall_store

    store = get_recall_store()
    turns = store.turns_for("claude_code:neuron-1:session-a")
    assert [turn.msg_id for turn in turns] == ["m1", "m2"]
    assert store.get_source("claude_code:neuron-1:session-a")["path"] == (
        "/home/neuron/.claude/session-a.jsonl"
    )


async def test_bank_ingest_rejects_the_forwarded_tool_shape(client_factory):
    """``{"args": ...}`` is the proxy shape; ingest is a bank batch and says so with a 400."""
    client = await client_factory()
    status, body = await _post(
        client, "/api/deck/brain/data/recall/ingest", {"args": {"project": "demo"}}
    )
    assert status == 400
    assert body["code"] == "invalid"


async def test_bank_ingest_validates_its_batch(client_factory):
    client = await client_factory()
    status, body = await _post(client, "/api/deck/brain/data/recall/ingest", {"sources": []})
    assert (status, body["code"]) == (400, "invalid")

    status, body = await _post(
        client,
        "/api/deck/brain/data/recall/ingest",
        {"sources": [{"source_id": "s1"}], "turns": ["not", "an", "object"]},
    )
    assert (status, body["code"]) == (400, "invalid")


async def test_recall_operations_require_an_args_object(client_factory):
    client = await client_factory()
    status, body = await _post(client, "/api/deck/brain/data/recall/search", {"args": "search me"})
    assert status == 400
    assert body["code"] == "invalid"


async def test_health_reports_the_authority(client_factory):
    client = await client_factory()
    status, body = await _post(client, "/api/deck/brain/data/health")
    assert (status, body["ok"]) == (200, True)
    assert body["data"]["role"] == "brain"
    assert body["data"]["authority"] is True


async def test_every_answer_uses_the_envelope_the_client_refuses_nothing_else(client_factory):
    """The client treats a non-envelope 200 as "did not run"; keep the shape."""
    client = await client_factory()
    for path, payload in (
        ("/api/deck/brain/data/memory/stats", {}),
        ("/api/deck/brain/data/memory/remember", {"fact": "envelope shape", "source": "t"}),
        ("/api/deck/brain/data/recall/decisions", {"args": {}}),
        ("/api/deck/brain/data/memory/nope", {}),
    ):
        response = await client.post(path, json=payload)
        body = await response.json()
        assert "ok" in body, (path, json.dumps(body)[:200])
        if body["ok"]:
            assert "data" in body
        else:
            assert isinstance(body.get("error"), str) and body["error"]


# ── the fleet and the plan ───────────────────────────────────


def test_every_forwarded_operation_names_a_real_handler():
    """A route may only name a tool the catalogue has; anything else is a 501 at runtime."""
    from navig.gateway.deck.routes import brain_data

    for label, ops in (
        ("memory", brain_data._MEMORY_OPS),
        ("runtime", brain_data._RUNTIME_OPS),
        ("goals", brain_data._GOALS_OPS),
    ):
        assert ops, label
        for op, tool in ops.items():
            assert brain_data._handler_for(tool) is not None, (label, op, tool)


async def test_a_mission_read_runs_the_brains_own_handler(client_factory):
    client = await client_factory()
    status, body = await _post(client, "/api/deck/brain/data/runtime/list_nodes", {})
    assert (status, body["ok"]) == (200, True)
    # The fleet comes from the runtime store, and this box has a registered node
    # ("outpost"), so an empty list is a property of a clean data dir rather than of the
    # route — it made this test fail only in a whole-suite run (measured 2026-10-30).
    # What the route must guarantee is the shape and that `count` matches `nodes`.
    assert set(body["data"]) == {"nodes", "count"}
    assert body["data"]["count"] == len(body["data"]["nodes"])


async def test_a_goal_written_over_the_route_is_read_back_from_the_same_store(client_factory):
    client = await client_factory()
    status, body = await _post(
        client, "/api/deck/brain/data/goals/add", {"description": "one plan, one Brain"}
    )
    assert (status, body["ok"]) == (200, True)
    assert body["data"]["ok"] is True

    status, body = await _post(client, "/api/deck/brain/data/goals/list", {})
    assert (status, body["ok"]) == (200, True)
    descriptions = [goal["description"] for goal in body["data"]["goals"]]
    assert descriptions == ["one plan, one Brain"]


async def test_an_unknown_runtime_operation_is_a_404_not_a_local_fallback(client_factory):
    client = await client_factory()
    status, body = await _post(client, "/api/deck/brain/data/runtime/launch_rocket", {})
    assert (status, body["code"]) == (404, "unknown_op")
