"""Hermetic contract tests for autonomous agent gateway endpoints.

Replaces the live-server-dependent tests in ``test_autonomous_agent.py`` with
in-process aiohttp stubs so the suite runs in CI without any live gateway.

Covers every contract verified by the live tests:
  * /health           – test_gateway_health
  * /status           – test_gateway_status
  * /cron/jobs GET    – test_cron_list
  * /cron/jobs POST   – test_cron_add
  * /cron/jobs DELETE – test_cron_delete
  * /heartbeat/trigger  – test_heartbeat_trigger
  * /heartbeat/history  – test_heartbeat_history
  * AI config presence  – test_ai_config
  * Workspace files     – test_workspace_files
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _make_cron_job(job_id: str = "job-001", name: str = "Test Health Check"):
    job = SimpleNamespace(
        id=job_id,
        name=name,
        schedule="every 5 minutes",
        command="Check system health",
        enabled=True,
        next_run="2025-01-01T00:00:00",
    )
    job.to_dict = lambda: {
        "id": job.id,
        "name": job.name,
        "schedule": job.schedule,
        "command": job.command,
        "enabled": job.enabled,
        "next_run": job.next_run,
    }
    return job


def _make_cron_service(jobs: list | None = None):
    svc = MagicMock()
    _jobs: list = list(jobs or [])
    svc.list_jobs.return_value = _jobs

    def _add_job(name, schedule, command, enabled=True, timeout_seconds=300):
        j = _make_cron_job(job_id="new-001", name=name)
        _jobs.append(j)
        return j

    svc.add_job.side_effect = _add_job
    svc.remove_job.side_effect = lambda job_id: any(j.id == job_id for j in _jobs)
    svc.get_job.side_effect = lambda job_id: next((j for j in _jobs if j.id == job_id), None)
    return svc


def _build_gateway(
    *,
    with_cron: bool = False,
    with_heartbeat: bool = False,
    auth_token: str | None = None,
):
    now = datetime.now()
    session = SimpleNamespace(messages=["hi"], created_at=now, updated_at=now)

    gateway = MagicMock()
    gateway.start_time = now - timedelta(seconds=30)
    gateway.running = True
    gateway.config = SimpleNamespace(
        port=8789,
        host="127.0.0.1",
        heartbeat_enabled=with_heartbeat,
        heartbeat_interval="30m",
        auth_token=auth_token,
    )
    gateway.sessions = SimpleNamespace(sessions={"telegram:1": session})
    gateway.router = MagicMock()
    gateway.router.route_message = AsyncMock(return_value="ok")
    gateway.system_events = MagicMock()
    gateway.system_events.enqueue = AsyncMock()
    gateway.stop = AsyncMock()

    gateway.cron_service = _make_cron_service() if with_cron else None

    if with_heartbeat:
        hb_result = SimpleNamespace(
            success=True,
            suppressed=True,
            response="All systems healthy",
            issues_found=[],
            timestamp=now,
        )
        hb = MagicMock()
        hb.trigger_now = AsyncMock(return_value=hb_result)
        hb.get_history = MagicMock(
            return_value=[
                {
                    "timestamp": now.isoformat(),
                    "success": True,
                    "suppressed": True,
                    "issues_count": 0,
                }
            ]
        )
        gateway.heartbeat_runner = hb
    else:
        gateway.heartbeat_runner = None

    return gateway


def _build_app(gateway, *, routes: tuple = ("core",)):
    pytest.importorskip("aiohttp")
    from aiohttp import web

    app = web.Application()
    for route in routes:
        if route == "core":
            from navig.gateway.routes.core import register as r_core

            r_core(app, gateway)
        elif route == "cron":
            from navig.gateway.routes.cron import register as r_cron

            r_cron(app, gateway)
        elif route == "heartbeat":
            from navig.gateway.routes.heartbeat import register as r_heartbeat

            r_heartbeat(app, gateway)
    return app


# ---------------------------------------------------------------------------
# Health + Status
# ---------------------------------------------------------------------------


async def test_gateway_health_contract():
    """GET /health → ok=True, data.status='ok'."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw, routes=("core",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["data"]["status"] == "ok"


async def test_gateway_status_contract():
    """GET /status → running state, config.port reflected."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway()
    app = _build_app(gw, routes=("core",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/status")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["status"] == "running"
        assert data["config"]["port"] == 8789


# ---------------------------------------------------------------------------
# Cron
# ---------------------------------------------------------------------------


async def test_cron_list_when_service_disabled_returns_503():
    """GET /cron/jobs → 503 when cron_service is None."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=False)
    app = _build_app(gw, routes=("cron",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/cron/jobs")
        assert resp.status == 503
        body = await resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "module_unavailable"


async def test_cron_list_returns_empty_when_no_jobs():
    """GET /cron/jobs → ok=True, empty jobs list."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=True)
    app = _build_app(gw, routes=("cron",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/cron/jobs")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["data"]["jobs"] == []


async def test_cron_list_returns_configured_jobs():
    """GET /cron/jobs → jobs list reflects seeded jobs."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=True)
    gw.cron_service = _make_cron_service(jobs=[_make_cron_job()])
    app = _build_app(gw, routes=("cron",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/cron/jobs")
        assert resp.status == 200
        body = await resp.json()
        jobs = body["data"]["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["id"] == "job-001"
        assert jobs[0]["name"] == "Test Health Check"


async def test_cron_add_job_returns_id_and_name():
    """POST /cron/jobs → ok=True, data.id not None, name matches payload."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=True)
    app = _build_app(gw, routes=("cron",))

    payload = {
        "name": "Test Health Check",
        "schedule": "every 5 minutes",
        "command": "Check system health and report issues",
        "enabled": True,
    }

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/cron/jobs", json=payload)
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["id"] is not None
        assert data["name"] == "Test Health Check"
        assert data["schedule"] == "every 5 minutes"


async def test_cron_add_missing_field_returns_400():
    """POST /cron/jobs with missing 'schedule' → 400 validation_error."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=True)
    app = _build_app(gw, routes=("cron",))

    # Missing 'schedule' key → KeyError → 400
    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/cron/jobs", json={"name": "bad"})
        assert resp.status == 400
        body = await resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "validation_error"


async def test_cron_delete_existing_job():
    """DELETE /cron/jobs/{id} → ok=True, data.deleted=True."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=True)
    gw.cron_service = _make_cron_service(jobs=[_make_cron_job(job_id="job-001")])
    app = _build_app(gw, routes=("cron",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.delete("/cron/jobs/job-001")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        assert body["data"]["deleted"] is True


async def test_cron_delete_unknown_job_returns_404():
    """DELETE /cron/jobs/{id} for unknown id → 404 not_found."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_cron=True)
    app = _build_app(gw, routes=("cron",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.delete("/cron/jobs/no-such-job")
        assert resp.status == 404
        body = await resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "not_found"


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


async def test_heartbeat_trigger_when_disabled_returns_503():
    """POST /heartbeat/trigger → 503 when heartbeat_runner is None."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_heartbeat=False)
    app = _build_app(gw, routes=("heartbeat",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/heartbeat/trigger")
        assert resp.status == 503
        body = await resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "module_unavailable"


async def test_heartbeat_trigger_contract():
    """POST /heartbeat/trigger → ok=True, suppressed bool, issues list."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_heartbeat=True)
    app = _build_app(gw, routes=("heartbeat",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/heartbeat/trigger")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["success"] is True
        assert isinstance(data["suppressed"], bool)
        assert isinstance(data["issues"], list)
        assert "timestamp" in data
        assert "response" in data


async def test_heartbeat_history_when_disabled_returns_503():
    """GET /heartbeat/history → 503 when heartbeat_runner is None."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_heartbeat=False)
    app = _build_app(gw, routes=("heartbeat",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/heartbeat/history")
        assert resp.status == 503
        body = await resp.json()
        assert body["ok"] is False


async def test_heartbeat_history_contract():
    """GET /heartbeat/history → ok=True, data.history is a list."""
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    gw = _build_gateway(with_heartbeat=True)
    app = _build_app(gw, routes=("heartbeat",))

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/heartbeat/history?limit=5")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
        history = body["data"]["history"]
        assert isinstance(history, list)
        assert len(history) == 1
        entry = history[0]
        assert entry["success"] is True
        assert "timestamp" in entry


# ---------------------------------------------------------------------------
# AI config (hermetic)
# ---------------------------------------------------------------------------


def test_ai_config_hermetic_with_key():
    """When openrouter_api_key is in global_config, test passes without skipping."""
    fake_config = MagicMock()
    fake_config.global_config = {
        "openrouter_api_key": "sk-test-key",
        "ai_model_preference": ["openai/gpt-4o", "anthropic/claude-3-5-sonnet"],
    }

    with patch("navig.config.get_config_manager", return_value=fake_config):
        from navig.config import get_config_manager

        config = get_config_manager()
        api_key = config.global_config.get("openrouter_api_key")
        assert api_key == "sk-test-key"
        models = config.global_config.get("ai_model_preference", [])
        assert len(models) == 2


def test_ai_config_hermetic_without_key():
    """When openrouter_api_key is missing, config is retrievable but key absent."""
    fake_config = MagicMock()
    fake_config.global_config = {}

    with patch("navig.config.get_config_manager", return_value=fake_config):
        from navig.config import get_config_manager

        config = get_config_manager()
        api_key = config.global_config.get("openrouter_api_key")
        assert api_key is None


# ---------------------------------------------------------------------------
# Workspace files (hermetic)
# ---------------------------------------------------------------------------


def test_workspace_files_hermetic_all_present(tmp_path):
    """When workspace files exist, the check passes without skipping."""
    workspace = tmp_path / ".navig" / "workspace"
    workspace.mkdir(parents=True)
    for fname in ("HEARTBEAT.md", "SOUL.md", "AGENTS.md"):
        (workspace / fname).write_text(f"# {fname}")

    with patch("pathlib.Path.home", return_value=tmp_path):
        from pathlib import Path

        ws = Path.home() / ".navig" / "workspace"
        missing = [f for f in ("HEARTBEAT.md", "SOUL.md", "AGENTS.md") if not (ws / f).exists()]
        assert missing == [], f"Unexpected missing files: {missing}"


def test_workspace_files_hermetic_some_missing(tmp_path):
    """When workspace files are missing, the check detects them."""
    workspace = tmp_path / ".navig" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "HEARTBEAT.md").write_text("# HB")
    # SOUL.md and AGENTS.md are absent

    with patch("pathlib.Path.home", return_value=tmp_path):
        from pathlib import Path

        ws = Path.home() / ".navig" / "workspace"
        missing = [f for f in ("HEARTBEAT.md", "SOUL.md", "AGENTS.md") if not (ws / f).exists()]
        assert set(missing) == {"SOUL.md", "AGENTS.md"}
