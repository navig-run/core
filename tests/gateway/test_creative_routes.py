"""Creative deck routes: shared status payload, card actions, publish policy.

Pattern: minimal aiohttp app with only the creative handlers mounted (the auth
middleware over /api/deck/* is exercised by the gateway's own route tests).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


def _app() -> web.Application:
    from navig.gateway.deck.routes.creative import (
        handle_creative_proposal_action,
        handle_creative_proposals,
        handle_creative_publish_get,
        handle_creative_publish_post,
        handle_creative_status,
    )

    app = web.Application()
    app.router.add_get("/api/deck/creative/proposals", handle_creative_proposals)
    app.router.add_get("/api/deck/creative/status", handle_creative_status)
    app.router.add_post("/api/deck/creative/proposals/{id}", handle_creative_proposal_action)
    app.router.add_get("/api/deck/creative/publish", handle_creative_publish_get)
    app.router.add_post("/api/deck/creative/publish", handle_creative_publish_post)
    return app


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    from navig.agent.proactive import user_state
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(user_state, "_tracker_instance", None)
    yield tmp_path
    reset_config_manager()
    ConfigSingleton._instance = None


async def _get(client, url):
    resp = await client.get(url)
    return resp.status, await resp.json()


async def _post(client, url, payload):
    resp = await client.post(url, json=payload)
    return resp.status, await resp.json()


def _mode(mode: str) -> None:
    from navig.agent.proactive.user_state import get_user_state_tracker

    assert get_user_state_tracker().set_preference("chat_mode", mode) is True


def _seed_proposal(slug: str = "route-build") -> dict:
    from navig.creative import proposals as prop
    from navig.creative.decide import ArtifactSpec, Decision

    d = Decision(
        verdict="create", reason="r", genuine_gap="g", plan=["p"],
        artifact=ArtifactSpec(kind="skill", slug=slug, title=slug,
                              description=f"run {slug}", commands=["navig x"]),
        benefit="b", complexity="low", verification="v",
    )
    p = prop.from_decision(d, source="detector:test", problem="R", evidence=[], goal=f"go {slug}")
    created, reason = prop.propose(p)
    assert created, reason
    return p


# ── reads ────────────────────────────────────────────────────────────────────


async def test_status_matches_the_cli_payload(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/creative/status")
    assert status == 200
    # Same shape the CLI prints (shared status_payload — no drift possible).
    assert set(body) == {"mode", "index", "last_scan_age_s", "proposals", "publish"}
    assert body["mode"] in ("standard", "creative")
    assert body["publish"]["mode"] == "local"


async def test_proposals_list_and_status_filter(isolated):
    p = _seed_proposal("list-me")
    async with TestClient(TestServer(_app())) as client:
        status, rows = await _get(client, "/api/deck/creative/proposals")
        assert status == 200 and [r["id"] for r in rows] == [p["id"]]
        status, open_rows = await _get(client, "/api/deck/creative/proposals?status=open")
        assert [r["id"] for r in open_rows] == [p["id"]]
        status, dismissed = await _get(client, "/api/deck/creative/proposals?status=dismissed")
        assert dismissed == []


# ── actions ──────────────────────────────────────────────────────────────────


async def test_unknown_proposal_404_and_bad_action_400(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/creative/proposals/deadbeefdeadbeef", {"action": "dismiss"}
        )
        assert status == 404
        p = _seed_proposal("bad-action")
        status, body = await _post(
            client, f"/api/deck/creative/proposals/{p['id']}", {"action": "explode"}
        )
        assert status == 400 and "unknown action" in body["error"]
        status, body = await _post(
            client, f"/api/deck/creative/proposals/{p['id']}", {"not_json": 1}
        )
        assert status == 400


async def test_dismiss_action_persists(isolated):
    p = _seed_proposal("dismiss-me")
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, f"/api/deck/creative/proposals/{p['id']}", {"action": "dismiss"}
        )
        assert status == 200 and body["ok"] is True
        status, rows = await _get(client, "/api/deck/creative/proposals?status=dismissed")
    assert [r["id"] for r in rows] == [p["id"]]


async def test_build_action_gated_by_mode_and_succeeds_when_creative(isolated):
    p = _seed_proposal("route-build")
    _mode("standard")
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, f"/api/deck/creative/proposals/{p['id']}", {"action": "build"}
        )
        # 409: understood but refused by the mode gate (not a server error).
        assert status == 409 and body["ok"] is False and "Standard" in body["reason"]

        _mode("creative")
        status, body = await _post(
            client, f"/api/deck/creative/proposals/{p['id']}", {"action": "build"}
        )
        assert status == 200 and body["ok"] is True, body

    from navig.creative import proposals as prop
    from navig.creative.build import skill_target_dir

    assert prop.get_proposal(p["id"])["status"] == "built"
    assert (skill_target_dir("route-build") / "SKILL.md").exists()


# ── publish policy ───────────────────────────────────────────────────────────


async def test_publish_policy_default_local_and_enable_roundtrip(isolated):
    dest = isolated / "collector"
    dest.mkdir()
    async with TestClient(TestServer(_app())) as client:
        status, body = await _get(client, "/api/deck/creative/publish")
        assert status == 200 and body["mode"] == "local"

        status, body = await _post(
            client,
            "/api/deck/creative/publish",
            {"mode": "repo", "repo": str(dest), "branch": "main", "auto_push": False},
        )
        assert status == 200 and body["mode"] == "repo"
        assert body["repos"] == [str(dest)]

        status, body = await _post(client, "/api/deck/creative/publish", {"mode": "local"})
        assert status == 200 and body["mode"] == "local"


async def test_publish_policy_rejects_bad_input(isolated):
    async with TestClient(TestServer(_app())) as client:
        status, body = await _post(
            client, "/api/deck/creative/publish", {"mode": "repo", "repo": ""}
        )
        assert status == 400
        status, body = await _post(
            client, "/api/deck/creative/publish",
            {"mode": "repo", "repo": "Z:/no/such/place"},
        )
        assert status == 400 and "does not exist" in body["error"]
        status, body = await _post(client, "/api/deck/creative/publish", {"mode": "banana"})
        assert status == 400
