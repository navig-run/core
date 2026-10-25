"""Deck API for Brain ownership — the same document the CLI prints.

Mounted on a bare aiohttp app through the REAL ``register_brain_routes`` so the paths
are exercised, with an isolated ``NAVIG_CONFIG_DIR`` and no gateway.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

MACHINE_A = "brn-aaaaaaaaaaaaaaaa"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text("brain:\n  role: brain\n", encoding="utf-8")
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "brain")
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)


@pytest.fixture()
def client_factory():
    from aiohttp.test_utils import TestClient, TestServer

    def _factory():
        from aiohttp import web

        from navig.gateway.deck.routes.brain import register_brain_routes

        app = web.Application()
        register_brain_routes(app)
        return TestClient(TestServer(app))

    return _factory


async def _get(client, path: str) -> tuple[int, dict]:
    response = await client.get(path)
    return response.status, await response.json()


async def _post(client, path: str, body: dict | None = None) -> tuple[int, dict]:
    kwargs = {"json": body} if body is not None else {}
    response = await client.post(path, **kwargs)
    return response.status, await response.json()


# ── reads ──────────────────────────────────────────────────────────────────


async def test_status_is_the_shared_document(client_factory) -> None:
    async with client_factory() as client:
        status, body = await _get(client, "/api/deck/brain")
    assert status == 200
    assert body["ok"] is True
    data = body["data"]
    assert data["state"] == "free"
    assert data["role"] == "brain"
    assert data["owner"] is None
    assert data["can_claim"] is True
    assert data["telegram"]["mode"] == "off"
    # The UI shows a warning, it does not invent one.
    assert data["host_dependency"]
    assert "workstation" in data["host_dependency"]


async def test_status_reports_another_machines_live_lease(client_factory) -> None:
    from navig.brain.lease import LeaseStore

    LeaseStore().acquire(oid=MACHINE_A, role="brain")
    async with client_factory() as client:
        status, body = await _get(client, "/api/deck/brain")
    assert status == 200
    data = body["data"]
    assert data["state"] == "held"
    assert data["owner"]["id"] == MACHINE_A
    assert data["you"]["is_owner"] is False
    assert data["can_claim"] is False


# ── actions ────────────────────────────────────────────────────────────────


async def test_claim_makes_this_machine_the_owner(client_factory) -> None:
    async with client_factory() as client:
        status, body = await _post(
            client, "/api/deck/brain/claim", {"reason": "from the Brain card"}
        )
        assert status == 200
        assert body["ok"] is True
        assert body["data"]["lease"]["reason"] == "from the Brain card"

        status, refreshed = await _get(client, "/api/deck/brain")
    assert refreshed["data"]["state"] == "held"
    assert refreshed["data"]["you"]["is_owner"] is True


async def test_claim_of_someone_elses_lease_is_a_409_with_the_cli_wording(
    client_factory,
) -> None:
    from navig.brain.lease import LeaseStore

    LeaseStore().acquire(oid=MACHINE_A, role="brain")
    async with client_factory() as client:
        status, body = await _post(client, "/api/deck/brain/claim")
    assert status == 409
    assert body["ok"] is False
    assert MACHINE_A in body["error"]
    assert "takeover" in body["error"]


async def test_release_then_claim_works(client_factory) -> None:
    async with client_factory() as client:
        await _post(client, "/api/deck/brain/claim")
        status, body = await _post(client, "/api/deck/brain/release", {"reason": "moving"})
        assert status == 200 and body["ok"] is True
        assert body["data"]["lease"]["released_at"] is not None
        status, again = await _post(client, "/api/deck/brain/claim")
    assert again["ok"] is True
    assert again["data"]["lease"]["epoch"] == 2


async def test_takeover_without_a_reason_is_refused(client_factory) -> None:
    async with client_factory() as client:
        status, body = await _post(client, "/api/deck/brain/takeover", {})
    assert status == 409
    assert body["ok"] is False
    assert "--reason" in body["error"]


async def test_takeover_records_the_audit(client_factory) -> None:
    from navig.brain.lease import LeaseStore

    store = LeaseStore()
    store.acquire(oid=MACHINE_A, role="brain")
    async with client_factory() as client:
        status, body = await _post(
            client,
            "/api/deck/brain/takeover",
            {"reason": "moving to the outpost", "force": True, "from_owner": MACHINE_A},
        )
    assert status == 200
    assert body["data"]["lease"]["takeovers"][-1]["reason"] == "moving to the outpost"


# ── migration plan (read-only) ─────────────────────────────────────────────


async def test_migrate_plan_needs_a_target(client_factory) -> None:
    async with client_factory() as client:
        status, body = await _post(client, "/api/deck/brain/migrate/plan", {})
    assert status == 400
    assert body["ok"] is False
    assert "target host" in body["error"]


async def test_migrate_plan_returns_the_reconcile_document(client_factory, monkeypatch) -> None:
    from navig.gateway.deck.routes import brain as routes

    def fake_plan(target: str) -> dict:
        return {
            "to": target,
            "from": "here",
            "you_are_brain": True,
            "role": "brain",
            "ok": False,
            "reconciled": ["spaces", "plugins"],
            "unreconciled": ["jobs missing on the target: courrier:scan"],
            "notes": ["1 other plugin stays on the source (not Brain duties)"],
        }

    monkeypatch.setattr(routes, "brain_migration_plan", fake_plan)
    async with client_factory() as client:
        status, body = await _post(client, "/api/deck/brain/migrate/plan", {"to": "outpost"})
    assert status == 200
    data = body["data"]
    assert data["to"] == "outpost"
    assert data["ok"] is False
    assert "courrier:scan" in data["unreconciled"][0]
    assert data["reconciled"] == ["spaces", "plugins"]


async def test_migrate_plan_reports_an_unreachable_target(client_factory, monkeypatch) -> None:
    from navig.gateway.deck.routes import brain as routes

    def boom(target: str) -> dict:
        raise RuntimeError("ssh: connect to host outpost port 22: Connection timed out")

    monkeypatch.setattr(routes, "brain_migration_plan", boom)
    async with client_factory() as client:
        status, body = await _post(client, "/api/deck/brain/migrate/plan", {"to": "outpost"})
    assert status == 503
    assert body["ok"] is False
    # The reason travels: a plan that cannot be read must never look like "nothing missing".
    assert "outpost" in body["error"]
    assert "data" not in body


def test_brain_migration_plan_is_the_migrate_parity_check(monkeypatch) -> None:
    """The card's verdict and ``navig brain migrate``'s refusal read the same document."""
    from navig.brain import migrate as migrate_mod
    from navig.brain import parity
    from navig.gateway.deck.routes import brain as routes

    monkeypatch.setattr(
        parity,
        "local_inventory",
        lambda *_a, **_k: {
            "jobs": [
                {
                    "name": "courrier:scan",
                    "enabled": True,
                    "command": "navig courrier scan --space company-paperwork",
                }
            ],
            "plugins": ["navig-msstore", "navig-display"],
            "spaces": ["company-paperwork-space"],
        },
    )

    class FakeRemote:
        def __init__(self, host: str, **_kw: object) -> None:
            self.host = host

        def target_inventory(self) -> dict:
            return {"jobs": [], "plugins": [], "spaces": []}

    monkeypatch.setattr(migrate_mod, "SshRemote", FakeRemote)
    plan = routes.brain_migration_plan("outpost")
    assert plan["to"] == "outpost"
    assert plan["ok"] is False
    joined = " ".join(plan["unreconciled"])
    assert "courrier:scan" in joined
    assert "company-paperwork" in joined
    assert "navig-msstore" in joined
    # A desktop-only plugin is a note, never a blocker — the same distinction the CLI makes.
    assert "navig-display" not in joined


# ── envelope discipline ────────────────────────────────────────────────────


async def test_every_response_is_json_with_the_envelope(client_factory) -> None:
    async with client_factory() as client:
        for path in ("/api/deck/brain",):
            response = await client.get(path)
            assert response.content_type == "application/json"
            body = await response.json()
            assert set(body) == {"ok", "data"}
            json.dumps(body)  # serialisable, always
