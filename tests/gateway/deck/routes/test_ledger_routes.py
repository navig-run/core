"""Tests for the Deck ledger routes (navig/gateway/deck/routes/ledger.py).

Covers GET /api/deck/ledger/verify (intact on a clean chain, broken-with-a-line
on a tampered one) and POST /api/deck/ledger/undo (404 unknown id, 422 missing
id, 400 bad JSON, 409 on a red/drifted/already-undone op, a side-effect-free
dry preview on confirm:false, and a chain-recorded perform on confirm:true).

The engines themselves are unit-tested in tests/ops/test_reversibility_undo.py
and tests/ops/test_skill_distill.py; this file proves the HTTP surface calls
them correctly, never mutates on a preview, and never bypasses a refusal.

Isolation: the recorder singleton is patched to a temp OperationRecorder so the
ledger is a throwaway file; config-touching undo paths patch
navig.config.get_config_manager with a narrow stub — nothing touches the
operator's real config.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


# ─────────────────────────── helpers ────────────────────────────────


def _make_recorder(history_dir: Path):
    from navig.operation_recorder import OperationRecorder

    return OperationRecorder(history_dir=history_dir)


def _ledger(history_dir: Path) -> Path:
    return history_dir / "operations.jsonl"


def _entries(path: Path) -> list[dict]:
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _record_config_change(
    recorder,
    key: str = "log_level",
    old_value="INFO",
    new_value="DEBUG",
    old_exists: bool = True,
):
    """Record a green (undoable) config_change the way the config-set seam does."""
    from navig.operation_recorder import OperationRecord, OperationStatus, OperationType

    record = OperationRecord(
        command=f"navig config set {key} {new_value}",
        operation_type=OperationType.CONFIG_CHANGE,
        status=OperationStatus.SUCCESS,
        args={"key": key},
        undo_data={
            "key": key,
            "old_value": old_value,
            "old_exists": old_exists,
            "new_value": new_value,
            "scope": "global",
        },
    )
    recorder.record(record)
    return record


def _record_plain(recorder, command: str = "navig run ls"):
    """Record a plain red (irreversible) local command."""
    from navig.operation_recorder import OperationRecord, OperationStatus, OperationType

    record = OperationRecord(
        command=command,
        operation_type=OperationType.LOCAL_COMMAND,
        status=OperationStatus.SUCCESS,
    )
    recorder.record(record)
    return record


class _StubConfigManager:
    """The narrow ConfigManager surface the undo engine touches for config ops."""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg if cfg is not None else {}
        self.saves = 0

    @property
    def global_config(self) -> dict:
        return self.cfg

    def refresh_global_config(self) -> dict:
        return self.cfg

    def _save_global_config(self, cfg: dict) -> None:
        self.cfg = cfg
        self.saves += 1

    def update_global_config(self, patch_dict: dict) -> None:
        self.cfg.update(patch_dict)
        self.saves += 1


def _app(recorder, monkeypatch):
    """A bare aiohttp app with just the two ledger routes.

    The ledger is isolated by patching the recorder singleton to *recorder*;
    the patch is stopped on cleanup so it never leaks into another test.
    """
    pytest.importorskip("aiohttp")
    from aiohttp import web

    from navig.gateway.deck.routes import ledger as mod

    patcher = patch(
        "navig.operation_recorder.get_operation_recorder", return_value=recorder
    )
    patcher.start()

    app = web.Application()
    app.router.add_get("/api/deck/ledger/verify", mod.handle_deck_ledger_verify)
    app.router.add_post("/api/deck/ledger/undo", mod.handle_deck_ledger_undo)

    async def _stop_patch(_app):  # aiohttp awaits cleanup callbacks
        patcher.stop()

    app.on_cleanup.append(_stop_patch)
    return app


# ─────────────────────────── verify ──────────────────────────────


async def test_verify_intact_on_clean_chain(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    hist = tmp_path / "hist"
    rec = _make_recorder(hist)
    _record_plain(rec, "navig host list")
    _record_config_change(rec)

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/verify")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["status"] == "intact"
        assert data["ok"] is True
        assert data["chained"] == 2
        assert data["verified"] == 2
        assert data["breaks"] == []
        assert data["guarantee"] == "tamper-evident"
        assert data["algorithm"] == "sha256"


async def test_verify_broken_reports_the_line(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    hist = tmp_path / "hist"
    rec = _make_recorder(hist)
    _record_plain(rec, "navig host list")
    _record_config_change(rec)
    _record_plain(rec, "navig db tables")

    # Tamper the second line's content in place — its stored hash no longer
    # matches its bytes, so verify must flag exactly that line.
    ledger = _ledger(hist)
    lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    tampered = json.loads(lines[1])
    tampered["command"] = "tampered"
    lines[1] = json.dumps(tampered)
    ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/verify")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["status"] == "broken"
        assert data["ok"] is False
        assert data["first_broken_line"] == 2
        assert any(b["line"] == 2 for b in data["breaks"])


async def test_verify_missing_ledger_is_honest(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")  # nothing recorded

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.get("/api/deck/ledger/verify")
        assert r.status == 200
        data = (await r.json())["data"]
        assert data["status"] == "missing"
        assert data["ok"] is True  # missing is not a failure


# ─────────────────────────── undo — refusals ──────────────────────────────


async def test_undo_unknown_id_is_404(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    _record_config_change(rec)

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.post("/api/deck/ledger/undo", json={"id": "op-nope"})
        assert r.status == 404
        body = await r.json()
        assert body["ok"] is False
        assert "not found" in body["error"]


async def test_undo_missing_id_is_422(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.post("/api/deck/ledger/undo", json={"confirm": True})
        assert r.status == 422
        assert "id" in (await r.json())["error"]


async def test_undo_bad_json_is_400(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.post(
            "/api/deck/ledger/undo",
            data=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status == 400


async def test_undo_refuses_red_op_with_409(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    rec = _make_recorder(tmp_path / "hist")
    red = _record_plain(rec, "navig run rm -rf /tmp/x")

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        r = await client.post("/api/deck/ledger/undo", json={"id": red.id, "confirm": True})
        assert r.status == 409
        body = await r.json()
        assert body["ok"] is False
        assert "red" in body["error"] or "not green" in body["error"]

    # The refusal touched nothing — no undo line appended.
    assert len(_entries(_ledger(tmp_path / "hist"))) == 1


async def test_undo_refuses_on_drift_with_409(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    hist = tmp_path / "hist"
    rec = _make_recorder(hist)
    target = _record_config_change(rec)  # recorded INFO -> DEBUG
    stub = _StubConfigManager({"log_level": "TRACE"})  # changed again since

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        with patch("navig.config.get_config_manager", return_value=stub):
            r = await client.post(
                "/api/deck/ledger/undo", json={"id": target.id, "confirm": True}
            )
            assert r.status == 409
            assert "changed since" in (await r.json())["error"]

    assert stub.cfg["log_level"] == "TRACE"  # untouched
    assert len(_entries(_ledger(hist))) == 1  # no undo line


# ─────────────────────────── undo — preview (no side effects) ─────────────


async def test_undo_preview_when_confirm_false_makes_no_change(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    hist = tmp_path / "hist"
    rec = _make_recorder(hist)
    target = _record_config_change(rec)
    stub = _StubConfigManager({"log_level": "DEBUG"})

    before = len(_entries(_ledger(hist)))

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        with patch("navig.config.get_config_manager", return_value=stub):
            r = await client.post("/api/deck/ledger/undo", json={"id": target.id})
            assert r.status == 200
            data = (await r.json())["data"]
            assert data["id"] == target.id
            assert data["requires_confirm"] is True
            assert data["label"] == "green"
            assert data["would"].startswith("restore config")

    # Preview mutated NOTHING — no ledger line, no config write.
    assert len(_entries(_ledger(hist))) == before
    assert stub.cfg["log_level"] == "DEBUG"
    assert stub.saves == 0


# ─────────────────────────── undo — perform (chain-recorded) ──────────────


async def test_undo_perform_appends_chained_undo_line(tmp_path, monkeypatch):
    from aiohttp.test_utils import TestClient, TestServer

    from navig.ledger_chain import verify_ledger

    hist = tmp_path / "hist"
    rec = _make_recorder(hist)
    target = _record_config_change(rec)
    stub = _StubConfigManager({"log_level": "DEBUG"})

    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        with patch("navig.config.get_config_manager", return_value=stub):
            r = await client.post(
                "/api/deck/ledger/undo", json={"id": target.id, "confirm": True}
            )
            assert r.status == 200
            data = (await r.json())["data"]
            assert data["id"] == target.id
            assert data["undo_id"]
            assert data["undone"].startswith("restore config")

    # The config value was actually restored.
    assert stub.cfg["log_level"] == "INFO"

    # A NEW undo-tagged line was appended and the chain still verifies.
    entries = _entries(_ledger(hist))
    assert len(entries) == 2
    assert entries[-1]["args"]["undo_of"] == target.id
    assert "undo" in entries[-1]["tags"]
    assert entries[-1]["status"] == "success"
    assert verify_ledger(_ledger(hist)).ok

    # A second undo of the same target is now refused (double-undo protection).
    async with TestClient(TestServer(_app(rec, monkeypatch))) as client:
        with patch("navig.config.get_config_manager", return_value=stub):
            again = await client.post(
                "/api/deck/ledger/undo", json={"id": target.id, "confirm": True}
            )
            assert again.status == 409
            assert "already undone" in (await again.json())["error"]
