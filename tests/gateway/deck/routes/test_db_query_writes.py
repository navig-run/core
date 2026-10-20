"""POST /api/deck/db/query: a write needs an explicit confirm, and a confirmed write is audited.

Regression: the route ran any SQL it was given (DROP included) with no check and no record.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    return tmp_path


@pytest.fixture()
def ran(monkeypatch):
    calls: list[str] = []

    async def fake_run(host, command, timeout=45.0):
        calls.append(command)
        return True, "1 row affected", ""

    from navig.gateway.deck.routes import database

    monkeypatch.setattr(database, "_run_remote", fake_run)
    return calls


def _app(audit_log=None):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes.database import handle_deck_db_query

    app = web.Application()
    if audit_log is not None:

        async def _allow_policy(action, actor, raw_input=""):
            # Minimal wiring for the authorize_deck step these tests were written
            # before: policy allows and audits nothing. Real policy consultation
            # for db.query (deny / 409-before-policy) is pinned in
            # test_remote_run_gate.py.
            return None

        app["gateway"] = type(
            "GW",
            (),
            {"audit_log": audit_log, "policy_check": staticmethod(_allow_policy)},
        )()
    app.router.add_post("/db/query", handle_deck_db_query)
    return app


async def _post(app, body):
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        r = await client.post("/db/query", json=body)
        return r.status, await r.json()


async def test_unconfirmed_write_is_refused_and_nothing_runs(isolated, ran):
    status, body = await _post(_app(), {"host": "h", "query": "DELETE FROM orders"})
    assert status == 409
    assert body["requires_confirmation"] is True
    assert body["level"] == "critical"
    assert body["statements"] == ["DELETE"]
    assert ran == []


async def test_a_truthy_string_is_not_a_confirmation(isolated, ran):
    status, _ = await _post(_app(), {"host": "h", "query": "UPDATE t SET x = 1", "confirm": "yes"})
    assert status == 409
    assert ran == []


async def test_confirmed_write_runs_and_is_audited_without_its_text(isolated, ran):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=isolated / "audit.jsonl")
    status, body = await _post(
        _app(log),
        {
            "host": "h",
            "database": "shop",
            "query": "UPDATE users SET pw = 'hunter2'",
            "confirm": True,
        },
    )
    assert status == 200
    assert body["data"]["ok"] is True
    assert body["data"]["audited"] is True
    assert len(ran) == 1

    raw = (isolated / "audit.jsonl").read_text(encoding="utf-8")
    assert "hunter2" not in raw, "the audit record must not carry the statement text"
    rec = json.loads(raw.strip().splitlines()[-1])
    assert rec["action"] == "db.query"
    assert rec["metadata"]["statements"] == ["UPDATE"]
    assert rec["metadata"]["host"] == "h" and rec["metadata"]["database"] == "shop"
    assert rec["input_hash"].startswith("sha256:")


async def test_read_runs_without_confirmation_and_is_not_audited(isolated, ran):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=isolated / "audit.jsonl")
    status, body = await _post(_app(log), {"host": "h", "query": "SELECT * FROM deleted_items"})
    assert status == 200
    assert body["data"]["level"] == "verbose"
    assert body["data"]["audited"] is None
    assert len(ran) == 1
    assert not (isolated / "audit.jsonl").exists()
