"""Script routes: starting and stopping is local-only; reading is not."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
pytest.importorskip("aiohttp")

from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from navig.gateway.deck.routes import projects as routes  # noqa: E402


@pytest.fixture()
def pid(project: Path) -> str:
    from navig.projects.model import add_project

    return add_project(project)["id"]


def _app() -> web.Application:
    app = web.Application()
    routes.register(app)
    return app


@pytest.mark.parametrize("headers", [{"X-Navig-Via": "lighthouse"}, {"CF-Ray": "abc"},
                                     {"X-Forwarded-For": "203.0.113.9"}])
async def test_start_and_stop_refuse_remote_origins(pid: str, headers: dict) -> None:
    async with TestClient(TestServer(_app())) as client:
        res = await client.post(f"/api/deck/projects/{pid}/scripts/start", json={"key": "npm:.:dev"},
                                headers=headers)
        assert res.status == 403
        res = await client.post("/api/deck/projects/runs/whatever/stop", headers=headers)
        assert res.status == 403
        # Reading stays available remotely.
        res = await client.get(f"/api/deck/projects/{pid}/scripts", headers=headers)
        assert res.status == 200


async def test_scripts_list_and_unknown_key(pid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async with TestClient(TestServer(_app())) as client:
        res = await client.get(f"/api/deck/projects/{pid}/scripts")
        body = await res.json()
        assert res.status == 200
        keys = {s["key"] for s in body["data"]["scripts"]}
        assert {"npm:.:dev", "npm:apps/web:dev"} <= keys
        deploy = next(s for s in body["data"]["scripts"] if s["name"] == "deploy:prod")
        assert deploy["danger"] is True and deploy["last_run"] is None
        assert deploy["run"] == "pnpm run deploy:prod"  # the command line survives

        res = await client.post(f"/api/deck/projects/{pid}/scripts/start", json={"key": "npm:.:nope"})
        assert res.status == 404
        res = await client.post(f"/api/deck/projects/{pid}/scripts/start", json={"command": "calc.exe"})
        assert res.status == 400  # a command is never accepted — only a key
        res = await client.get("/api/deck/projects/runs/does-not-exist")
        assert res.status == 404
