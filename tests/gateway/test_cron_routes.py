"""Integration tests for the low-level /cron/jobs routes (navig/gateway/routes/cron.py)
over aiohttp: CRUD, the add-time schedule/field validation (#340) + required/empty/timeout
guards, and lifecycle 404s / service-unavailable 503.

Auth is bypassed by a fake gateway whose ``config.auth_token`` is None
(``require_bearer_auth`` returns None → open); the real engine is a detached
``CronService`` over a tmp dir. Run-now never spawns a subprocess:
``_execute_job_command`` is monkeypatched.
"""

from __future__ import annotations

import types

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("aiohttp")

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from navig.gateway.routes import cron as cron_routes
from navig.scheduler.cron_service import CronService


def _app(cron_service) -> web.Application:
    """A minimal app with the cron routes and a fake, unauthenticated gateway."""
    gw = types.SimpleNamespace(
        cron_service=cron_service,
        config=types.SimpleNamespace(auth_token=None),  # → require_bearer_auth passes
    )
    app = web.Application()
    cron_routes.register(app, gw)
    return app


@pytest.fixture()
def cron_service(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    # gateway=None → detached instance (skips live registration + legacy migration).
    return CronService(gateway=None, storage_path=tmp_path / "scheduler")


async def _post(client: TestClient, path: str, body: dict | None = None):
    res = await client.post(path, json=body or {})
    return res.status, await res.json()


async def _get(client: TestClient, path: str):
    res = await client.get(path)
    return res.status, await res.json()


async def _delete(client: TestClient, path: str):
    res = await client.delete(path)
    return res.status, await res.json()


async def test_add_list_roundtrip(cron_service):
    async with TestClient(TestServer(_app(cron_service))) as client:
        status, body = await _post(client, "/cron/jobs", {
            "name": "Health check", "schedule": "0 9 * * 1-5", "command": "navig host list",
        })
        assert status == 200 and body["ok"] is True
        job = body["data"]
        assert job["id"] and job["name"] == "Health check"
        assert job["next_run"]  # computed for enabled jobs

        status, body = await _get(client, "/cron/jobs")
        assert status == 200
        jobs = body["data"]["jobs"]
        assert len(jobs) == 1 and jobs[0]["command"] == "navig host list"


async def test_add_rejects_out_of_range_schedule(cron_service):
    # #340 at the low-level route: an out-of-range field parses the char-set but
    # never fires — rejected with a specific reason, not stored as a silent +1h job.
    async with TestClient(TestServer(_app(cron_service))) as client:
        status, body = await _post(client, "/cron/jobs", {
            "name": "x", "schedule": "0 25 * * *", "command": "navig --version",
        })
        assert status == 400 and "out of range" in body["error"]

        status, _ = await _post(client, "/cron/jobs", {
            "name": "x", "schedule": "whenever vibes", "command": "navig --version",
        })
        assert status == 400

        _, body = await _get(client, "/cron/jobs")
        assert body["data"]["jobs"] == []  # nothing leaked


async def test_add_rejects_missing_or_empty_fields(cron_service):
    async with TestClient(TestServer(_app(cron_service))) as client:
        for bad in (
            {},
            {"name": "x"},                                             # no schedule/command
            {"name": "", "schedule": "daily", "command": "c"},         # empty name
            {"name": "x", "schedule": "daily", "command": "  "},       # whitespace command
        ):
            status, body = await _post(client, "/cron/jobs", bad)
            assert status == 400, bad
            assert "required" in body["error"], bad


async def test_add_rejects_bad_timeout(cron_service):
    async with TestClient(TestServer(_app(cron_service))) as client:
        for bad_timeout in (-5, 0, "abc"):
            status, body = await _post(client, "/cron/jobs", {
                "name": "x", "schedule": "daily", "command": "navig --version",
                "timeout": bad_timeout,
            })
            assert status == 400, bad_timeout
            assert "timeout" in body["error"], bad_timeout


async def test_get_enable_disable_delete_lifecycle(cron_service):
    async with TestClient(TestServer(_app(cron_service))) as client:
        _, body = await _post(client, "/cron/jobs", {
            "name": "Backup", "schedule": "daily", "command": "navig backup run",
        })
        job_id = body["data"]["id"]

        status, body = await _get(client, f"/cron/jobs/{job_id}")
        assert status == 200 and body["data"]["name"] == "Backup"

        status, body = await _post(client, f"/cron/jobs/{job_id}/disable")
        assert status == 200 and body["data"]["disabled"] is True

        status, body = await _post(client, f"/cron/jobs/{job_id}/enable")
        assert status == 200 and body["data"]["enabled"] is True

        status, body = await _delete(client, f"/cron/jobs/{job_id}")
        assert status == 200 and body["data"]["deleted"] is True

        # Every verb 404s on an unknown / deleted id.
        status, _ = await _get(client, f"/cron/jobs/{job_id}")
        assert status == 404
        status, _ = await _delete(client, f"/cron/jobs/{job_id}")
        assert status == 404
        status, _ = await _post(client, f"/cron/jobs/{job_id}/enable")
        assert status == 404


async def test_run_now(cron_service, monkeypatch):
    async def fake_exec(self, job):
        return f"ran {job.name}"

    monkeypatch.setattr(CronService, "_execute_job_command", fake_exec)

    async with TestClient(TestServer(_app(cron_service))) as client:
        _, body = await _post(client, "/cron/jobs", {
            "name": "Echo", "schedule": "daily", "command": "navig --version",
        })
        job_id = body["data"]["id"]

        status, body = await _post(client, f"/cron/jobs/{job_id}/run")
        assert status == 200
        assert body["data"]["success"] is True
        assert body["data"]["output"] == "ran Echo"

        status, _ = await _post(client, "/cron/jobs/ghost/run")
        assert status == 404


async def test_service_unavailable_returns_503():
    # No cron_service on the gateway → 503, not a crash.
    async with TestClient(TestServer(_app(None))) as client:
        status, body = await _get(client, "/cron/jobs")
        assert status == 503 and body["ok"] is False
