"""POST/GET Environment membership over the real /api/deck/projects routes.

Phase 2 of Unified Environments: the Projects hub stays a pure view of the Project
Manager — membership rides in on read from Environment manifests, and the write
route validates core-side (unknown project → 404, non-Environment / bad op → 400).
Uses the same conftest fixtures as test_routes.py (isolated registry + a project).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
pytest.importorskip("aiohttp")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from navig.gateway.deck.routes import projects as routes  # noqa: E402


def _app() -> web.Application:
    app = web.Application()
    routes.register(app)
    return app


async def _json(res):
    return res.status, await res.json()


def _mk_env(root: Path, sid: str, kind: str = "environment") -> Path:
    from navig.spaces import registry

    d = root / "cfg" / "spaces" / f"{sid}-space"
    d.mkdir(parents=True, exist_ok=True)
    (d / ".navig").mkdir(exist_ok=True)
    (d / ".navig" / "space.json").write_text(
        json.dumps({"id": sid, "name": sid.title(), "kind": kind, "version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    registry.register(d, id=sid, name=sid.title(), source="external", enabled=True)
    return d


async def test_attach_list_get_detach_over_http(isolated: Path, project: Path) -> None:
    from navig.projects.model import add_project

    pid = add_project(project)["id"]
    _mk_env(isolated, "dev")

    async with TestClient(TestServer(_app())) as client:
        # attach → 200 with the updated membership list
        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "attach", "environment": "dev"}))
        assert status == 200, body
        assert body["data"] == {"id": pid, "environments": ["dev"]}

        # idempotent attach
        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "attach", "environment": "dev"}))
        assert status == 200 and body["data"]["environments"] == ["dev"]

        # membership is layered on BOTH reads (the view contract)
        status, body = await _json(await client.get(f"/api/deck/projects/{pid}"))
        assert status == 200 and body["data"]["environments"] == ["dev"]

        status, body = await _json(await client.get("/api/deck/projects"))
        row = next(r for r in body["data"] if r.get("id") == pid)
        assert row["environments"] == ["dev"]

        # detach → empty again
        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "detach", "environment": "dev"}))
        assert status == 200 and body["data"]["environments"] == []


async def test_membership_errors_map_to_statuses(isolated: Path, project: Path) -> None:
    from navig.projects.model import add_project

    pid = add_project(project)["id"]
    _mk_env(isolated, "dev")
    _mk_env(isolated, "notes", kind="area")

    async with TestClient(TestServer(_app())) as client:
        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "explode", "environment": "dev"}))
        assert status == 400 and "op must be" in body["error"]

        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "attach"}))
        assert status == 400 and "environment" in body["error"]

        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "attach", "environment": "notes"}))
        assert status == 400 and "not an environment" in body["error"]

        status, body = await _json(await client.post(
            f"/api/deck/projects/{pid}/environments", json={"op": "attach", "environment": "ghost-env"}))
        assert status == 400 and "not a registered space" in body["error"]

        status, body = await _json(await client.post(
            "/api/deck/projects/no-such-project/environments",
            json={"op": "attach", "environment": "dev"}))
        assert status == 404
