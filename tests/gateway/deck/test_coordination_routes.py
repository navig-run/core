"""Coordination deck routes — operator-only, one envelope, wires the service."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aiohttp")

from navig.gateway.deck.routes import coordination  # noqa: E402


class _Req(dict):
    def __init__(self, body=None, *, query=None, match_info=None, local=True, user_id=1):  # noqa: ANN001, ANN003
        super().__init__(deck_auth_local=local, deck_user_id=user_id)
        self._body = body
        self.query = query or {}
        self.match_info = match_info or {}
        self.path = "/api/deck/coordination/test"
        self.app: dict = {}

    async def json(self):  # noqa: ANN201
        return self._body


def _body(resp):  # noqa: ANN001
    return json.loads(resp.body)


async def test_inbox_is_operator_only(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.gateway.deck.auth._deck_config",
        {"allowed_users": [42], "allowed_users_configured": True},
    )
    refused = await coordination.handle_coordination_inbox(
        _Req(query={"recipient": "session:1"}, local=False, user_id=99)
    )
    assert refused.status == 403


async def test_send_wires_the_service_and_rejects_a_missing_participant(monkeypatch) -> None:
    seen = {}

    def fake_send(*args, **kwargs):  # noqa: ANN002, ANN003
        seen["args"] = args
        seen["kwargs"] = kwargs
        return {"id": "m1", "delivery_state": "persisted"}

    monkeypatch.setattr("navig.coordination.send", fake_send)
    resp = await coordination.handle_coordination_send(
        _Req({"sender": "agent:a", "recipient": "session:1", "kind": "inform", "content": {}})
    )
    assert resp.status == 201
    assert _body(resp)["data"]["id"] == "m1"
    assert seen["args"][0] == "agent:a"

    missing = await coordination.handle_coordination_send(_Req({"content": {}}))
    assert missing.status == 400


async def test_transition_requires_a_known_verb(monkeypatch) -> None:
    resp = await coordination.handle_coordination_transition(
        _Req(match_info={"id": "m1", "verb": "explode"})
    )
    assert resp.status == 404


async def test_tick_runs_one_delivery_pass(monkeypatch) -> None:
    from navig.coordination import delivery

    monkeypatch.setattr(
        delivery, "tick",
        lambda: [delivery.DeliveryOutcome("m1", "session:1", "delivered")],
    )
    resp = await coordination.handle_coordination_tick(_Req())
    assert resp.status == 200
    assert _body(resp)["data"]["count"] == 1
    assert _body(resp)["data"]["outcomes"][0]["state"] == "delivered"


async def test_delegate_enqueues_on_the_gateway_task_queue(monkeypatch) -> None:
    added: list = []

    class _Queue:
        async def add(self, task):  # noqa: ANN001
            added.append(task)

    class _Gateway:
        task_queue = _Queue()

    class _Task:
        id = "t1"
        handler = "coord:delegate"
        dependencies: list[str] = []

    monkeypatch.setattr("navig.coordination.delegation.delegate", lambda *a, **k: _Task())

    req = _Req({"parent": "agent:lead", "assignee": "agent:worker", "operation": "verify"})
    req.app = {"gateway": _Gateway()}
    resp = await coordination.handle_coordination_delegate(req)

    assert resp.status == 202
    assert len(added) == 1
    assert _body(resp)["data"]["task_id"] == "t1"


async def test_delegate_requires_parent_assignee_and_operation() -> None:
    resp = await coordination.handle_coordination_delegate(_Req({"operation": "verify"}))
    assert resp.status == 400
