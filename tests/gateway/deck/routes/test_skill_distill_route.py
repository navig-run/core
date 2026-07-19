"""Tests for the Deck distill route (navig/gateway/deck/routes/skill_distill.py).

Covers POST /api/deck/skills/distill (preview by default, confirm-gated write,
409-on-exists, 422-nothing-distillable, 400-bad-duration, --ops selection, the
secret sweep surviving into the preview) and GET /api/deck/ledger/recent (window
filter + limit cap). The engine itself is unit-tested in tests/ops/
test_skill_distill.py; this file proves the HTTP surface calls it correctly and
never writes on a preview.

The route resolves the ledger via get_operation_recorder() and writes to
store_dir(); both are isolated per test — the recorder singleton is patched to a
temp OperationRecorder, and NAVIG_STORE_DIR points writes at tmp_path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ─────────────────────────── helpers ────────────────────────────────


def _make_recorder(history_dir: Path):
    from navig.operation_recorder import OperationRecorder

    return OperationRecorder(history_dir=history_dir)


def _ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _record(
    recorder,
    command: str,
    *,
    minutes_ago: float = 5,
    status: str = "success",
    op_type: str = "local_command",
    host: str | None = None,
    error: str = "",
    exit_code: int = 0,
):
    from navig.operation_recorder import OperationRecord, OperationStatus, OperationType

    rec = OperationRecord(
        command=command,
        timestamp=_ts(minutes_ago),
        operation_type=OperationType(op_type),
        status=OperationStatus(status),
        host=host,
        error=error,
        exit_code=exit_code,
    )
    recorder.record(rec)
    return rec


def _seed(recorder):
    _record(recorder, "navig config set ui.theme dark", minutes_ago=30, op_type="config_change")
    _record(
        recorder,
        "navig docker restart web",
        minutes_ago=20,
        status="failed",
        error="no such container",
        exit_code=2,
    )
    _record(recorder, "navig docker restart web-1", minutes_ago=10, op_type="docker_command")


def _app(recorder, store_dir: Path, monkeypatch):
    """A bare aiohttp app with just the distill + ledger routes.

    Isolates the ledger (patched recorder singleton) and the write target
    (NAVIG_STORE_DIR) so nothing touches the operator's real store.
    """
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import skill_distill as mod

    monkeypatch.setenv("NAVIG_STORE_DIR", str(store_dir))
    patcher = patch(
        "navig.operation_recorder.get_operation_recorder", return_value=recorder
    )
    patcher.start()

    app = web.Application()
    app.router.add_post("/api/deck/skills/distill", mod.handle_deck_skill_distill)
    app.router.add_get("/api/deck/ledger/recent", mod.handle_deck_ledger_recent)

    async def _stop_patch(_app):  # aiohttp awaits cleanup callbacks
        patcher.stop()

    app.on_cleanup.append(_stop_patch)
    return app


# ─────────────────────────── preview (dry-run) ──────────────────────────────


async def test_preview_returns_draft_without_writing(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _seed(rec)
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post(
            "/api/deck/skills/distill", json={"last": "2h", "name": "demo-flow"}
        )
        assert r.status == 200
        body = await r.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["dry_run"] is True
        assert data["slug"] == "demo-flow"
        assert data["markdown"].startswith("---")
        assert data["counts"]["steps"] == 2
        assert data["counts"]["pitfalls"] == 1
        assert data["safety"] == "elevated"
        assert data["exists"] is False
        assert "navig skill lint" in data["lint_hint"]

    # dry-run wrote nothing
    assert not (store / "skills" / "demo-flow" / "SKILL.md").exists()


async def test_preview_is_the_default_when_dry_run_omitted(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _seed(rec)
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post("/api/deck/skills/distill", json={"name": "demo-flow"})
        assert r.status == 200
        assert (await r.json())["data"]["dry_run"] is True
    assert not (store / "skills" / "demo-flow" / "SKILL.md").exists()


# ─────────────────────────── write ──────────────────────────────


async def test_write_persists_lint_clean_skill(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _seed(rec)
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post(
            "/api/deck/skills/distill",
            json={"last": "2h", "name": "demo-flow", "dry_run": False},
        )
        assert r.status == 201
        data = (await r.json())["data"]
        assert data["dry_run"] is False
        assert data["overwritten"] is False
        skill_file = Path(data["path"])
        assert skill_file.is_file()
        assert skill_file == store / "skills" / "demo-flow" / "SKILL.md"

    # The written draft passes the REAL linter.
    from typer.testing import CliRunner

    from navig.commands.skills import skills_app

    lint = CliRunner().invoke(skills_app, ["lint", str(skill_file.parent)], obj={})
    assert lint.exit_code == 0, lint.output


async def test_write_refuses_existing_without_force_then_overwrites(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _seed(rec)
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        first = await client.post(
            "/api/deck/skills/distill", json={"name": "demo-flow", "dry_run": False}
        )
        assert first.status == 201

        clash = await client.post(
            "/api/deck/skills/distill", json={"name": "demo-flow", "dry_run": False}
        )
        assert clash.status == 409
        cbody = await clash.json()
        assert cbody["ok"] is False
        assert "force" in cbody["hint"]

        forced = await client.post(
            "/api/deck/skills/distill",
            json={"name": "demo-flow", "dry_run": False, "force": True},
        )
        assert forced.status == 200
        assert (await forced.json())["data"]["overwritten"] is True


# ─────────────────────────── selection + refusals ──────────────────────────────


async def test_ops_selection_distills_exactly_those(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _seed(rec)
    only = _record(rec, "navig host list", minutes_ago=3, op_type="read_query")
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post(
            "/api/deck/skills/distill", json={"ops": [only.id], "name": "one-op"}
        )
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["counts"]["steps"] == 1
        assert "navig host list" in data["markdown"]
        assert "docker restart" not in data["markdown"]


async def test_empty_window_is_422(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")  # empty ledger
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post("/api/deck/skills/distill", json={"last": "1h"})
        assert r.status == 422
        body = await r.json()
        assert body["ok"] is False
        assert "nothing to distill" in body["error"]
        assert "ledger" in body["hint"]


async def test_invalid_duration_is_400(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post("/api/deck/skills/distill", json={"last": "2 fortnights"})
        assert r.status == 400
        assert "invalid duration" in (await r.json())["error"]


async def test_bad_json_body_is_400(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post(
            "/api/deck/skills/distill",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 400


async def test_secret_never_leaks_into_preview(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _record(rec, "navig host list", minutes_ago=20, op_type="read_query")
    _record(
        rec,
        "navig db connect --password hunter2 --name prod",
        minutes_ago=10,
        op_type="local_command",
    )
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.post("/api/deck/skills/distill", json={"name": "secret-check"})
        data = (await r.json())["data"]
        assert "hunter2" not in data["markdown"]
        assert "<secret>" in data["markdown"]


# ─────────────────────────── ledger recent ──────────────────────────────


async def test_ledger_recent_windowed(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _record(rec, "navig config set old 1", minutes_ago=300, op_type="config_change")
    _record(rec, "navig host list", minutes_ago=5, op_type="read_query")
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/recent?last=2h")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["count"] == 1
        assert data["operations"][0]["command"] == "navig host list"
        assert data["window"] == "last 2h"


async def test_ledger_recent_limit_and_order(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    for i in range(5):
        _record(rec, f"navig host list {i}", minutes_ago=50 - i, op_type="read_query")
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/recent?limit=2")
        data = (await r.json())["data"]
        assert data["count"] == 2
        # newest first
        assert data["operations"][0]["command"] == "navig host list 4"


async def test_ledger_recent_bad_duration_is_400(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/recent?last=nope")
        assert r.status == 400


async def test_ledger_recent_redacts_raw_secret_at_display_time(tmp_path, monkeypatch):
    """A pre-T-068 ledger line with a raw secret must not surface over the Deck.

    The record-time redaction can be bypassed (an old entry, a missed pattern),
    and this route is Lighthouse-reachable — so the command is re-redacted for
    display, defense-in-depth.
    """
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    # Simulate a raw, un-redacted historical entry the record-time sweep missed.
    _record(
        rec,
        "navig db connect --password hunter2 --name prod",
        minutes_ago=5,
        op_type="local_command",
    )
    store = tmp_path / "store"

    async with TestClient(TestServer(_app(rec, store, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/recent")
        assert r.status == 200
        cmd = (await r.json())["data"]["operations"][0]["command"]
        assert "hunter2" not in cmd
        assert "REDACTED" in cmd
