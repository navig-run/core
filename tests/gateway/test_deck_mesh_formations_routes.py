"""Contract tests for the /api/deck/mesh + /api/deck/formations routes (slice B6).

Exercises the OS Mesh tile's transport over real HTTP (TestClient): the mesh
read aliases (registry-backed peers view + non-blocking scan kick), the
formations engine surface (list / active / detail over a fixture formations
root), and the Council run/history loop (engine call faked; persistence real).

All filesystem state is isolated: NAVIG_DATA_DIR → tmp_path (council history
lands under it), the formations loader is pointed at a tmp roots list via
``set_formations_roots``, the FormationRegistry singleton is reset so no other
test's cache leaks in, and the active profile comes from a tmp cwd's
``.navig/profile.json``.
"""

from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import navig.gateway.deck.routes.formations as formations_routes
import navig.gateway.deck.routes.mesh as mesh_routes
import navig.gateway.routes.mesh as mesh_root_routes

FORMATION_JSON = {
    "id": "test_squad",
    "name": "Test Squad",
    "version": "1.0.0",
    "description": "A two-agent fixture formation.",
    "agents": ["alpha", "beta"],
    "default_agent": "alpha",
    "aliases": ["squad"],
}

# Agent schema requires system_prompt with minLength 100 — keep fixtures real.
_LONG_PROMPT = (
    "You evaluate every question from your specialized perspective, stay inside "
    "your scope, disagree openly when the evidence demands it, and always close "
    "with a concrete recommendation the team can act on."
)

AGENT_ALPHA = {
    "id": "alpha",
    "name": "Alpha",
    "role": "Architect",
    "traits": ["precise"],
    "personality": "Measured and exact.",
    "scope": ["architecture"],
    "system_prompt": f"You are Alpha, the architect. {_LONG_PROMPT}",
    "council_weight": 1.5,
}

AGENT_BETA = {
    "id": "beta",
    "name": "Beta",
    "role": "Operator",
    "traits": ["calm"],
    "personality": "Hands on the live machinery.",
    "scope": ["operations"],
    "system_prompt": f"You are Beta, the operator. {_LONG_PROMPT}",
}

FAKE_COUNCIL_RESULT = {
    "pack": "test_squad",
    "formation": "Test Squad",
    "question": "Ship it?",
    "rounds": [
        {
            "round": 1,
            "responses": [
                {
                    "agent": "alpha",
                    "name": "Alpha",
                    "role": "Architect",
                    "response": "Yes — the seams hold.\nCONFIDENCE: 0.9",
                    "confidence": 0.9,
                    "duration_ms": 12,
                },
            ],
        }
    ],
    "final_decision": "Ship it. Alpha and Beta agree the seams hold.",
    "overall_confidence": 0.9,
    "total_duration_ms": 25,
    "agents_count": 2,
}


class _FakeEventQueue:
    """Collects gateway broadcasts the way system_events would fan them out."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event_type: str, payload: dict | None = None, **_kw) -> str:
        self.events.append((event_type, payload or {}))
        return f"evt_{len(self.events)}"

    def council_payloads(self) -> list[dict]:
        return [p for (t, p) in self.events if t == "council_update"]

    async def drain(self, count: int, timeout_s: float = 2.0) -> list[dict]:
        """Wait for `count` council_update payloads (emits are scheduled onto
        the loop via run_coroutine_threadsafe, so they land shortly AFTER the
        deliberation task itself completes)."""
        for _ in range(int(timeout_s / 0.01)):
            if len(self.council_payloads()) >= count:
                break
            await asyncio.sleep(0.01)
        return self.council_payloads()


def _app(gateway: object | None = None) -> web.Application:
    app = web.Application()
    if gateway is not None:
        app["gateway"] = gateway
    app.router.add_get("/api/deck/mesh/peers", mesh_routes.handle_mesh_peers)
    app.router.add_post("/api/deck/mesh/scan", mesh_routes.handle_mesh_scan)
    app.router.add_get("/api/deck/formations", formations_routes.handle_formations_list)
    app.router.add_get("/api/deck/formations/active", formations_routes.handle_formation_active)
    app.router.add_get("/api/deck/formations/detail", formations_routes.handle_formation_detail)
    app.router.add_post(
        "/api/deck/formations/council/run", formations_routes.handle_council_run
    )
    app.router.add_get(
        "/api/deck/formations/council/history", formations_routes.handle_council_history
    )
    return app


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """Isolated data dir + fixture formations root + active profile in a tmp cwd."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))

    # No council guard/task may leak in from a failed run in another test.
    formations_routes._active_council_id = None
    formations_routes._council_task = None

    # Fixture formation (flat .agent.json format — the loader's legacy path).
    formations_root = tmp_path / "formations"
    fdir = formations_root / "test_squad"
    agents = fdir / "agents"
    agents.mkdir(parents=True)
    (fdir / "formation.json").write_text(json.dumps(FORMATION_JSON), encoding="utf-8")
    (agents / "alpha.agent.json").write_text(json.dumps(AGENT_ALPHA), encoding="utf-8")
    (agents / "beta.agent.json").write_text(json.dumps(AGENT_BETA), encoding="utf-8")

    from navig.formations import loader
    from navig.formations.registry import FormationRegistry

    loader.set_formations_roots([formations_root])
    # No boot-time cache may leak in from other tests — force the loader path.
    monkeypatch.setattr(FormationRegistry, "_instance", None)

    # Active profile: tmp cwd carrying .navig/profile.json → test_squad.
    root = tmp_path / "space"
    (root / ".navig").mkdir(parents=True)
    (root / ".navig" / "profile.json").write_text(
        json.dumps({"version": 1, "profile": "test_squad"}), encoding="utf-8"
    )
    monkeypatch.chdir(root)

    yield root

    loader.clear_formations_roots()


async def _ok_data(resp) -> dict:
    body = await resp.json()
    assert body["ok"] is True, body
    return body["data"]


# ── Mesh aliases ──────────────────────────────────────────────────────────────


class _StubRegistry:
    def to_api_dict(self) -> dict:
        return {
            "self": {"node_id": "navig-test-self", "hostname": "test-pc", "health": "online"},
            "peers": [
                {
                    "node_id": "navig-linux-peer",
                    "hostname": "peer-01",
                    "health": "online",
                    "os": "linux",
                    "load": 0.2,
                }
            ],
        }


async def test_mesh_peers_serves_registry_view(workspace, monkeypatch):
    import navig.mesh.registry as mesh_registry

    monkeypatch.setattr(mesh_registry, "get_registry", lambda *_a, **_k: _StubRegistry())
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/mesh/peers")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data["self"]["node_id"] == "navig-test-self"
    assert data["peers"][0]["hostname"] == "peer-01"


async def test_mesh_peers_degrades_to_error_envelope(workspace, monkeypatch):
    """A registry failure surfaces as {ok:false} — never an unhandled 500 page."""
    import navig.mesh.registry as mesh_registry

    def _boom(*_a, **_k):
        raise RuntimeError("mesh off")

    monkeypatch.setattr(mesh_registry, "get_registry", _boom)
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/mesh/peers")
        assert resp.status == 500
        body = await resp.json()
    assert body["ok"] is False
    assert "mesh off" in body["error"]


# ── Mesh scan (discovery nudge) ───────────────────────────────────────────────


class _FakeDiscovery:
    """Stands in for the gateway's live MeshDiscovery — records announce()."""

    def __init__(self, result: bool = True):
        self.result = result
        self.announce_calls = 0

    async def announce(self) -> bool:
        self.announce_calls += 1
        return self.result


async def test_mesh_scan_graceful_without_live_discovery(workspace):
    """No gateway / no MeshDiscovery instance → 200 scanning:false, never a crash."""
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/deck/mesh/scan")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data == {"scanning": False, "reason": "mesh not running"}


async def test_mesh_scan_graceful_when_loop_not_running(workspace):
    """announce() returning False (loop stopped / socket gone) degrades the same way."""
    fake = _FakeDiscovery(result=False)
    gw = SimpleNamespace(_mesh_discovery=fake, storage_dir=None)
    async with TestClient(TestServer(_app(gateway=gw))) as client:
        resp = await client.post("/api/deck/mesh/scan")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data["scanning"] is False
    assert fake.announce_calls == 1


async def test_mesh_scan_announces_and_returns_snapshot(workspace, monkeypatch):
    """A live discovery gets exactly one announce(); the registry snapshot rides back."""
    import navig.mesh.registry as mesh_registry

    monkeypatch.setattr(mesh_registry, "get_registry", lambda *_a, **_k: _StubRegistry())
    fake = _FakeDiscovery(result=True)
    gw = SimpleNamespace(_mesh_discovery=fake, storage_dir=None)
    async with TestClient(TestServer(_app(gateway=gw))) as client:
        resp = await client.post("/api/deck/mesh/scan")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data["scanning"] is True
    assert fake.announce_calls == 1
    assert data["self"]["node_id"] == "navig-test-self"
    assert data["peers"][0]["node_id"] == "navig-linux-peer"


async def test_root_mesh_scan_graceful_without_discovery(workspace):
    """Gateway-root POST /mesh/discovery/scan: mesh disabled → 200 scanning:false."""
    gw = SimpleNamespace(storage_dir=None)  # no _mesh_discovery attribute at all
    app = web.Application()
    app.router.add_post("/mesh/discovery/scan", mesh_root_routes._scan(gw))
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mesh/discovery/scan")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data == {"scanning": False, "reason": "mesh not running"}


async def test_root_mesh_scan_announces_live_instance(workspace, monkeypatch):
    """Gateway-root scan drives the LIVE gw._mesh_discovery, not a throwaway."""
    # The root module binds get_registry at import — patch its own reference.
    monkeypatch.setattr(
        mesh_root_routes, "get_registry", lambda *_a, **_k: _StubRegistry()
    )
    fake = _FakeDiscovery(result=True)
    gw = SimpleNamespace(_mesh_discovery=fake, storage_dir=None)
    app = web.Application()
    app.router.add_post("/mesh/discovery/scan", mesh_root_routes._scan(gw))
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/mesh/discovery/scan")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data["scanning"] is True
    assert fake.announce_calls == 1
    assert data["peers"][0]["hostname"] == "peer-01"


# ── Formations ────────────────────────────────────────────────────────────────


async def test_formations_list_marks_active(workspace):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/formations")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data["active_id"] == "test_squad"
    rows = {f["id"]: f for f in data["formations"]}
    assert rows["test_squad"]["active"] is True
    assert rows["test_squad"]["agents_count"] == 2
    assert rows["test_squad"]["aliases"] == ["squad"]


async def test_formation_active_carries_agents_without_prompts(workspace):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/formations/active")
        assert resp.status == 200
        data = await _ok_data(resp)
    formation = data["formation"]
    assert formation["id"] == "test_squad"
    assert formation["default_agent"] == "alpha"
    assert formation["loaded_count"] == 2
    agents = {a["id"]: a for a in formation["agents"]}
    assert agents["alpha"]["role"] == "Architect"
    assert agents["alpha"]["council_weight"] == 1.5
    assert agents["beta"]["loaded"] is True
    # Composed system prompts never ride the wire (payload + prompt hygiene).
    assert all("system_prompt" not in a for a in formation["agents"])


async def test_formation_active_none_when_unresolvable(workspace):
    (workspace / ".navig" / "profile.json").unlink()  # default profile won't resolve
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/formations/active")
        assert resp.status == 200
        data = await _ok_data(resp)
    assert data["formation"] is None


async def test_formation_detail_by_alias_and_404(workspace):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/formations/detail", params={"id": "squad"})
        assert resp.status == 200
        data = await _ok_data(resp)
        assert data["formation"]["id"] == "test_squad"

        resp = await client.get("/api/deck/formations/detail", params={"id": "ghost"})
        assert resp.status == 404

        resp = await client.get("/api/deck/formations/detail")
        assert resp.status == 400


# ── Council ───────────────────────────────────────────────────────────────────


async def test_council_run_requires_question(workspace):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post("/api/deck/formations/council/run", json={})
        assert resp.status == 400


async def test_council_run_409_without_active_formation(workspace):
    (workspace / ".navig" / "profile.json").unlink()
    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/deck/formations/council/run", json={"question": "Ship it?"}
        )
        assert resp.status == 409


async def test_council_run_persists_and_history_reads_back(workspace, monkeypatch, tmp_path):
    import navig.formations.council as council_mod

    calls: list[dict] = []

    def _fake_run_council(formation, question, rounds, timeout_per_agent, on_event=None):
        calls.append({
            "formation": formation.id,
            "question": question,
            "rounds": rounds,
            "timeout": timeout_per_agent,
        })
        return dict(FAKE_COUNCIL_RESULT)

    monkeypatch.setattr(council_mod, "run_council", _fake_run_council)

    async with TestClient(TestServer(_app())) as client:
        resp = await client.post(
            "/api/deck/formations/council/run",
            json={"question": "Ship it?", "rounds": 99, "timeout_s": 1},
        )
        assert resp.status == 200
        data = await _ok_data(resp)
        assert data["persisted"] is True
        record_id = data["id"]
        assert record_id
        # The correlation id IS the history id (blocking mode includes it too).
        assert data["council_id"] == record_id
        assert data["result"]["final_decision"].startswith("Ship it.")
        # Inputs were clamped to the engine's safe envelope before the call.
        assert calls == [
            {"formation": "test_squad", "question": "Ship it?", "rounds": 5, "timeout": 5.0}
        ]
        # The record physically lives under the ISOLATED data dir.
        stored = tmp_path / "data" / "council" / f"{record_id}.json"
        assert stored.is_file()

        # History feed lists it (summary shape) …
        resp = await client.get("/api/deck/formations/council/history")
        data = await _ok_data(resp)
        assert data["count"] == 1
        entry = data["entries"][0]
        assert entry["id"] == record_id
        assert entry["question"] == "Ship it?"
        assert entry["rounds"] == 1
        assert "responses" not in entry  # summaries stay light

        # … and ?id= returns the full record.
        resp = await client.get(
            "/api/deck/formations/council/history", params={"id": record_id}
        )
        data = await _ok_data(resp)
        assert data["record"]["rounds"][0]["responses"][0]["agent"] == "alpha"


# ── Streamed council (broadcast transport — council_update events) ────────────


async def test_council_run_stream_emits_ordered_events_and_persists(
    workspace, monkeypatch, tmp_path
):
    """stream:true → 202 kickoff, ordered council_update broadcasts (each
    carrying the council_id), a route-enriched terminal `done` with the
    history id, and the record persisted exactly as in blocking mode."""
    import navig.formations.council as council_mod

    def _fake_run_council(formation, question, rounds, timeout_per_agent, on_event=None):
        assert on_event is not None, "streaming route must thread on_event into the engine"
        on_event({"type": "round_started", "round": 1, "of": 1})
        on_event({
            "type": "agent_response",
            "round": 1,
            "agent": "alpha",
            "name": "Alpha",
            "role": "Architect",
            "confidence": 0.9,
            "summary": "Yes — the seams hold.",
            "duration_ms": 12,
        })
        on_event({"type": "synthesis_started"})
        on_event({"type": "done"})  # engine-level done is withheld by the route
        return dict(FAKE_COUNCIL_RESULT)

    monkeypatch.setattr(council_mod, "run_council", _fake_run_council)
    queue = _FakeEventQueue()
    gateway = SimpleNamespace(system_events=queue)

    async with TestClient(TestServer(_app(gateway))) as client:
        resp = await client.post(
            "/api/deck/formations/council/run",
            json={"question": "Ship it?", "stream": True},
        )
        assert resp.status == 202
        data = await _ok_data(resp)
        council_id = data["council_id"]
        assert council_id
        assert data["started"] is True
        assert "result" not in data  # nothing blocks on the kickoff

        # Drain the background deliberation deterministically.
        task = formations_routes._council_task
        assert task is not None
        await task

        payloads = await queue.drain(4)
        assert [p["type"] for p in payloads] == [
            "round_started",
            "agent_response",
            "synthesis_started",
            "done",
        ]
        # Every event correlates to THIS run.
        assert all(p["council_id"] == council_id for p in payloads)
        done = payloads[-1]
        assert done["history_id"] == council_id
        assert done["persisted"] is True
        assert done["overall_confidence"] == 0.9
        assert "synthesis_error" not in done  # healthy runs are never badged
        # Progress events stay light: summaries only, never full responses.
        agent_evt = payloads[1]
        assert agent_evt["summary"] == "Yes — the seams hold."
        assert "response" not in agent_evt

        # The record persisted under the SAME id the events carried …
        stored = tmp_path / "data" / "council" / f"{council_id}.json"
        assert stored.is_file()

        # … and history serves it exactly like a blocking run's record.
        resp = await client.get(
            "/api/deck/formations/council/history", params={"id": council_id}
        )
        data = await _ok_data(resp)
        assert data["record"]["final_decision"].startswith("Ship it.")

        # The run guard is released — a follow-up run is accepted.
        assert formations_routes._active_council_id is None


async def test_council_run_stream_done_carries_synthesis_error_badge(
    workspace, monkeypatch, tmp_path
):
    """A run whose synthesis failed still persists — and the route's enriched
    terminal `done` carries `synthesis_error: true` so UIs can badge the
    record instead of presenting the failure sentence as the real answer."""
    import navig.formations.council as council_mod

    failed_result = {
        **FAKE_COUNCIL_RESULT,
        "final_decision": (
            "Synthesis failed (429 too many requests) — see the individual agent responses above."
        ),
        "synthesis_error": "429 too many requests",
    }

    def _fake_run_council(formation, question, rounds, timeout_per_agent, on_event=None):
        on_event({"type": "synthesis_started"})
        on_event({"type": "done", "synthesis_error": True})  # withheld by the route
        return dict(failed_result)

    monkeypatch.setattr(council_mod, "run_council", _fake_run_council)
    queue = _FakeEventQueue()
    gateway = SimpleNamespace(system_events=queue)

    async with TestClient(TestServer(_app(gateway))) as client:
        resp = await client.post(
            "/api/deck/formations/council/run",
            json={"question": "Ship it?", "stream": True},
        )
        assert resp.status == 202
        await formations_routes._council_task

        payloads = await queue.drain(2)
        assert [p["type"] for p in payloads] == ["synthesis_started", "done"]
        done = payloads[-1]
        assert done["synthesis_error"] is True
        assert done["persisted"] is True
        record_id = done["history_id"]
        assert record_id

        # The record persisted with the honest failure shape …
        resp = await client.get("/api/deck/formations/council/history", params={"id": record_id})
        data = await _ok_data(resp)
        record = data["record"]
        assert record["synthesis_error"] == "429 too many requests"
        assert record["final_decision"].startswith("Synthesis failed (")
        assert "[ERROR" not in record["final_decision"]

        # … and a healthy run's done event never carries the badge
        # (see test_council_run_stream_emits_ordered_events_and_persists).
        assert formations_routes._active_council_id is None


async def test_council_run_409_while_another_is_running(workspace, monkeypatch):
    """Only ONE council per gateway: concurrent runs (streamed or blocking)
    409 until the active deliberation finishes, then runs are accepted again."""
    import navig.formations.council as council_mod

    release = threading.Event()

    def _slow_run_council(formation, question, rounds, timeout_per_agent, on_event=None):
        assert release.wait(timeout=10), "test released the run"
        return dict(FAKE_COUNCIL_RESULT)

    monkeypatch.setattr(council_mod, "run_council", _slow_run_council)
    gateway = SimpleNamespace(system_events=_FakeEventQueue())

    async with TestClient(TestServer(_app(gateway))) as client:
        resp = await client.post(
            "/api/deck/formations/council/run",
            json={"question": "One", "stream": True},
        )
        assert resp.status == 202

        # A second streamed run AND a blocking run are both rejected.
        resp2 = await client.post(
            "/api/deck/formations/council/run",
            json={"question": "Two", "stream": True},
        )
        assert resp2.status == 409
        body = await resp2.json()
        assert "already running" in body["error"]
        resp3 = await client.post(
            "/api/deck/formations/council/run", json={"question": "Three"}
        )
        assert resp3.status == 409

        release.set()
        await formations_routes._council_task

        # Guard released — the next run goes through.
        resp4 = await client.post(
            "/api/deck/formations/council/run", json={"question": "Four"}
        )
        assert resp4.status == 200
        data = await _ok_data(resp4)
        assert data["result"]["final_decision"].startswith("Ship it.")


async def test_council_run_stream_engine_failure_emits_error(workspace, monkeypatch):
    """An engine crash mid-stream broadcasts a terminal `error` event and
    releases the single-council guard — the tab never spins forever."""
    import navig.formations.council as council_mod

    def _boom(formation, question, rounds, timeout_per_agent, on_event=None):
        raise RuntimeError("model offline")

    monkeypatch.setattr(council_mod, "run_council", _boom)
    queue = _FakeEventQueue()
    gateway = SimpleNamespace(system_events=queue)

    async with TestClient(TestServer(_app(gateway))) as client:
        resp = await client.post(
            "/api/deck/formations/council/run",
            json={"question": "Ship it?", "stream": True},
        )
        assert resp.status == 202
        await formations_routes._council_task
        payloads = await queue.drain(1)

    assert [p["type"] for p in payloads] == ["error"]
    assert "model offline" in payloads[0]["message"]
    assert formations_routes._active_council_id is None


async def test_council_history_rejects_traversal_ids(workspace):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get(
            "/api/deck/formations/council/history", params={"id": "../evil"}
        )
        assert resp.status == 400
        resp = await client.get(
            "/api/deck/formations/council/history", params={"id": "missing-0000"}
        )
        assert resp.status == 404


async def test_council_history_empty_state(workspace):
    async with TestClient(TestServer(_app())) as client:
        resp = await client.get("/api/deck/formations/council/history")
        data = await _ok_data(resp)
    assert data == {"entries": [], "count": 0}
