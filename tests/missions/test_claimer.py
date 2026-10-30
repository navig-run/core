"""The node-side claimer: who wins a mission, what runs, and what closes it.

The three properties worth pinning, each because getting it wrong is silent:

* a **lost claim is never executed** — the loser must not run work somebody else owns;
* an **empty queue is idle, not broken** — a polling node would otherwise log errors all night;
* **every claimed mission closes with a receipt**, including one whose capability this node
  cannot run, because a mission left in ``running`` is invisible work.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from navig.contracts.mission import Mission, MissionStatus
from navig.contracts.store import RuntimeStore
from navig.missions.claimer import (
    CAPABILITY_READINESS_PROBE,
    ClaimOutcome,
    HttpTransport,
    MissionClaimer,
    StoreTransport,
    UnknownCapability,
    execute_mission,
    known_capabilities,
)


def _mission(mission_id: str, *, node_id: str = "node-a", created: str = "2026-10-28T00:00:00Z",
             capability: str = CAPABILITY_READINESS_PROBE, metadata=None) -> Mission:
    return Mission.from_dict(
        {
            "mission_id": mission_id,
            "title": f"mission {mission_id}",
            "node_id": node_id,
            "capability": capability,
            "payload": {"note": "test"},
            "metadata": metadata or {},
            "tags": [],
            "created_at": created,
        }
    )


class _Transport:
    """In-memory transport that records what the claimer asked for."""

    def __init__(self, queued, *, claims=None, receipt="receipt-1"):
        self.queued = list(queued)
        self._claims = dict(claims or {})
        self._receipt = receipt
        self.listed: list[str] = []
        self.claim_calls: list[str] = []
        self.completions: list[dict] = []

    async def list_queued(self, node_id: str):
        self.listed.append(node_id)
        return [m for m in self.queued if m.node_id == node_id]

    async def claim(self, mission_id: str) -> bool:
        self.claim_calls.append(mission_id)
        return self._claims.get(mission_id, True)

    async def complete(self, mission_id, *, succeeded, result=None, error=None) -> str:
        self.completions.append(
            {"mission_id": mission_id, "succeeded": succeeded, "result": result, "error": error}
        )
        return f"{self._receipt}-{mission_id}"


def _run(claimer: MissionClaimer) -> ClaimOutcome:
    return asyncio.run(claimer.run_once())


# ── the claim fence ───────────────────────────────────────────────────────────


def test_an_empty_queue_is_idle_and_not_an_error():
    transport = _Transport([])
    outcome = _run(MissionClaimer(transport, node_id="node-a"))

    assert outcome.claimed is False
    assert outcome.idle is True
    assert outcome.as_dict()["ok"] is True
    assert transport.claim_calls == []


def test_it_claims_the_oldest_queued_mission_first():
    transport = _Transport(
        [
            _mission("newer", created="2026-10-28T02:00:00Z"),
            _mission("older", created="2026-10-28T00:00:00Z"),
        ]
    )
    outcome = _run(MissionClaimer(transport, node_id="node-a", execute=lambda m: {"done": True}))

    assert outcome.mission_id == "older"
    assert transport.claim_calls == ["older"]


def test_a_lost_claim_is_never_executed():
    executed: list[str] = []
    transport = _Transport([_mission("m1")], claims={"m1": False})
    outcome = _run(
        MissionClaimer(transport, node_id="node-a", execute=lambda m: executed.append(m.mission_id))
    )

    assert executed == []
    assert outcome.claimed is False
    assert outcome.executed is False
    assert transport.completions == []
    assert outcome.as_dict()["ok"] is True


def test_a_lost_claim_moves_on_to_the_next_mission():
    transport = _Transport(
        [_mission("taken", created="2026-10-28T00:00:00Z"),
         _mission("free", created="2026-10-28T01:00:00Z")],
        claims={"taken": False},
    )
    seen: list[str] = []
    outcome = _run(
        MissionClaimer(transport, node_id="node-a", execute=lambda m: seen.append(m.mission_id))
    )

    assert seen == ["free"]
    assert outcome.mission_id == "free"
    assert outcome.succeeded is True


def test_a_mission_for_another_node_is_not_listed_here():
    transport = _Transport([_mission("mine", node_id="node-a"), _mission("theirs", node_id="node-b")])
    outcome = _run(MissionClaimer(transport, node_id="node-a", execute=lambda m: {}))

    assert transport.listed == ["node-a"]
    assert outcome.mission_id == "mine"


# ── completion ────────────────────────────────────────────────────────────────


def test_a_successful_run_completes_with_its_result_and_receipt():
    transport = _Transport([_mission("m1")])
    outcome = _run(MissionClaimer(transport, node_id="node-a", execute=lambda m: {"answer": 42}))

    assert outcome.succeeded is True
    assert outcome.executed is True
    assert outcome.receipt_id == "receipt-1-m1"
    assert transport.completions == [
        {"mission_id": "m1", "succeeded": True, "result": {"answer": 42}, "error": None}
    ]
    assert outcome.as_dict()["result"] == {"answer": 42}


def test_an_unknown_capability_fails_the_mission_rather_than_pretending():
    transport = _Transport([_mission("m1", capability="teleport")])
    outcome = _run(MissionClaimer(transport, node_id="node-a"))

    assert outcome.succeeded is False
    assert outcome.executed is False
    assert "teleport" in outcome.error
    assert outcome.receipt_id == "receipt-1-m1"
    completion = transport.completions[0]
    assert completion["succeeded"] is False
    assert "teleport" in completion["error"]
    assert outcome.as_dict()["ok"] is False


def test_an_exception_in_execution_still_closes_the_mission():
    transport = _Transport([_mission("m1")])

    def boom(_mission: Mission):
        raise RuntimeError("the tool died")

    outcome = _run(MissionClaimer(transport, node_id="node-a", execute=boom))

    assert outcome.succeeded is False
    assert outcome.error == "RuntimeError: the tool died"
    assert transport.completions[0]["succeeded"] is False
    assert "the tool died" in transport.completions[0]["error"]
    assert outcome.receipt_id.endswith("m1")


def test_an_async_executor_is_awaited():
    transport = _Transport([_mission("m1")])

    async def execute(_mission: Mission):
        await asyncio.sleep(0)
        return {"async": True}

    outcome = _run(MissionClaimer(transport, node_id="node-a", execute=execute))

    assert outcome.result == {"async": True}
    assert transport.completions[0]["result"] == {"async": True}


# ── against the real store ────────────────────────────────────────────────────


def test_store_transport_claims_a_queued_mission_only_once(tmp_path):
    store = RuntimeStore(tmp_path)
    store.create_mission(_mission("m1"))
    transport = StoreTransport(store)

    first = asyncio.run(transport.claim("m1"))
    second = asyncio.run(transport.claim("m1"))

    assert first is True
    assert second is False, "a second claim must lose: the state machine already left queued"
    assert store.get_mission("m1").status is MissionStatus.RUNNING


def test_store_transport_lists_only_queued_missions_for_its_node(tmp_path):
    store = RuntimeStore(tmp_path)
    store.create_mission(_mission("queued", created="2026-10-28T01:00:00Z"))
    store.create_mission(_mission("other-node", node_id="node-b"))
    running = store.create_mission(_mission("running", created="2026-10-28T00:30:00Z"))
    store.advance_mission(running.mission_id, "start")

    listed = asyncio.run(StoreTransport(store).list_queued("node-a"))

    assert [mission.mission_id for mission in listed] == ["queued"]
    assert listed[0].created_at == "2026-10-28T01:00:00Z"


def test_store_transport_completion_signs_a_receipt(tmp_path):
    store = RuntimeStore(tmp_path)
    store.create_mission(_mission("m1"))
    transport = StoreTransport(store)
    asyncio.run(transport.claim("m1"))

    receipt_id = asyncio.run(
        transport.complete("m1", succeeded=True, result={"rows": 3})
    )

    receipts = list(store.list_receipts())
    assert [r.receipt_id for r in receipts] == [receipt_id]
    assert receipts[0].artifacts["signature"]


def test_the_claimer_end_to_end_against_a_store(tmp_path):
    store = RuntimeStore(tmp_path)
    store.create_mission(_mission("m1", metadata={"repo_root": str(tmp_path)}))
    outcome = _run(
        MissionClaimer(StoreTransport(store), node_id="node-a", execute=lambda m: {"ok": True})
    )

    assert outcome.succeeded is True
    assert store.get_mission("m1").status is MissionStatus.SUCCEEDED
    assert store.get_mission("m1").result == {"ok": True}


# ── the HTTP transport (no server) ────────────────────────────────────────────


class _Response:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self, **_kwargs) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Session:
    def __init__(self, calls: list, answer):
        self._calls = calls
        self._answer = answer

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def request(self, method, url, json=None, headers=None):
        self._calls.append({"method": method, "url": url, "json": json, "headers": headers})
        status, body = self._answer(method, url)
        return _Response(status, body)


def _transport(answer):
    calls: list = []
    return HttpTransport("http://brain:8789", "secret", session_factory=lambda **_kw: _Session(calls, answer)), calls


def test_http_listing_asks_for_this_node_and_only_queued_missions():
    transport, calls = _transport(
        lambda method, url: (200, json.dumps({"data": {"missions": [
            {**_mission("m1").to_dict()},
        ]}}))
    )
    listed = asyncio.run(transport.list_queued("node-a"))

    assert [mission.mission_id for mission in listed] == ["m1"]
    assert "/runtime/missions" in calls[0]["url"]
    assert "node_id=node-a" in calls[0]["url"]
    assert "status=queued" in calls[0]["url"]
    assert calls[0]["headers"]["Authorization"] == "Bearer secret"


def test_http_claim_reads_a_conflict_as_a_lost_claim():
    transport, calls = _transport(lambda method, url: (409, "{}"))
    won = asyncio.run(transport.claim("m1"))

    assert won is False
    assert calls[0]["json"] == {"action": "start"}


def test_http_complete_speaks_the_route_contract_not_the_store_spelling():
    """`POST /runtime/missions/{id}/complete` takes {outcome, output, error}.

    Sending the store's `succeeded`/`result` spelling is silently lossy: `outcome` defaults to
    "success", so the mission reports success while `output` is dropped and the receipt's digest
    covers nothing. Measured against the live gateway on 2026-10-28 — the mission came back
    `succeeded` with `result: null`.
    """
    transport, calls = _transport(
        lambda method, url: (201, json.dumps({"data": {"receipt_id": "r-9"}}))
    )
    receipt_id = asyncio.run(transport.complete("m1", succeeded=True, result={"a": 1}))

    assert receipt_id == "r-9"
    assert calls[0]["json"] == {"outcome": "success", "output": {"a": 1}}


def test_http_complete_names_a_failure_outcome():
    transport, calls = _transport(
        lambda method, url: (201, json.dumps({"data": {"receipt_id": "r-10"}}))
    )
    asyncio.run(transport.complete("m1", succeeded=False, error="it broke"))

    assert calls[0]["json"] == {"outcome": "failure", "error": "it broke"}


# ── what this node admits it can run ─────────────────────────────────────────


def test_the_registry_names_its_capabilities_and_refuses_the_rest():
    assert CAPABILITY_READINESS_PROBE in known_capabilities()
    with pytest.raises(UnknownCapability) as excinfo:
        execute_mission(_mission("m1", capability="does-not-exist"))
    assert "does-not-exist" in str(excinfo.value)


def test_a_completed_mission_is_flushed_so_a_fresh_store_sees_it(tmp_path):
    """The transport owns persistence, not the caller.

    Measured while proving the worker end to end: `complete_mission` mutates in memory, and
    without an explicit flush the next `RuntimeStore()` loads the mission still `queued` and
    finds no receipt — work that happened and left no trace.
    """
    store = RuntimeStore(tmp_path)
    store.create_mission(_mission("m1"))
    transport = StoreTransport(store)
    assert asyncio.run(transport.claim("m1")) is True
    receipt_id = asyncio.run(transport.complete("m1", succeeded=True, result={"ok": True}))

    reloaded = RuntimeStore(tmp_path)
    mission = reloaded.get_mission("m1")
    assert mission is not None
    assert mission.status is MissionStatus.SUCCEEDED
    assert [r.receipt_id for r in reloaded.list_receipts()] == [receipt_id]
