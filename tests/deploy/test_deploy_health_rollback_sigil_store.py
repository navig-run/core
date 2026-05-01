"""Batch 75 — deploy/health, deploy/rollback, identity/sigil_store."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from navig.deploy.models import BackupConfig, HealthConfig, SnapshotRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _remote_ok(stdout="200", returncode=0):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


def _remote_fail(stderr="error", returncode=1):
    return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)


# ---------------------------------------------------------------------------
# navig.deploy.health — HealthChecker
# ---------------------------------------------------------------------------

class TestHealthCheckerDryRun:
    def _make(self, cfg=None) -> "HealthChecker":
        from navig.deploy.health import HealthChecker
        cfg = cfg or HealthConfig(url="http://localhost/health")
        return HealthChecker(cfg, server_config={}, remote_ops=MagicMock(), dry_run=True)

    def test_dry_run_returns_true(self):
        hc = self._make()
        ok, msg = hc.check()
        assert ok is True
        assert "DRY RUN" in msg

    def test_dry_run_shows_url(self):
        hc = self._make(HealthConfig(url="http://app/ping"))
        ok, msg = hc.check()
        assert "http://app/ping" in msg


class TestHealthCheckerNoConfig:
    def test_no_url_or_command_returns_true(self):
        from navig.deploy.health import HealthChecker
        hc = HealthChecker(HealthConfig(), server_config={}, remote_ops=MagicMock())
        ok, msg = hc.check()
        assert ok is True
        assert "skipped" in msg.lower() or "No health check" in msg


class TestHealthCheckerHttpCheck:
    def _make_hc(self, remote_ops):
        from navig.deploy.health import HealthChecker
        cfg = HealthConfig(url="http://app/health", expected_status=200, retries=1, interval_seconds=0)
        return HealthChecker(cfg, server_config={}, remote_ops=remote_ops)

    def test_http_200_returns_true(self):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_ok(stdout="200")
        hc = self._make_hc(remote)
        ok, msg = hc.check()
        assert ok is True
        assert "200" in msg

    def test_http_500_returns_false(self):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_ok(stdout="500")
        hc = self._make_hc(remote)
        ok, msg = hc.check()
        assert ok is False
        assert "500" in msg

    def test_curl_failure_returns_false(self):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_fail()
        hc = self._make_hc(remote)
        ok, msg = hc.check()
        assert ok is False

    def test_invalid_method_returns_false(self):
        from navig.deploy.health import HealthChecker
        cfg = HealthConfig(url="http://app/health", method="INJECT && evil", retries=1)
        hc = HealthChecker(cfg, server_config={}, remote_ops=MagicMock())
        ok, msg = hc.check()
        assert ok is False
        assert "Invalid HTTP method" in msg


class TestHealthCheckerCommandCheck:
    def _make_hc(self, remote_ops):
        from navig.deploy.health import HealthChecker
        cfg = HealthConfig(command="systemctl is-active myapp", retries=1, interval_seconds=0)
        return HealthChecker(cfg, server_config={}, remote_ops=remote_ops)

    def test_exit_0_returns_true(self):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_ok()
        ok, msg = self._make_hc(remote).check()
        assert ok is True

    def test_exit_nonzero_returns_false(self):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_fail(returncode=3)
        ok, msg = self._make_hc(remote).check()
        assert ok is False


class TestHealthCheckerRetries:
    def test_retry_succeeds_on_second_attempt(self):
        from navig.deploy.health import HealthChecker
        cfg = HealthConfig(url="http://app/health", expected_status=200, retries=2, interval_seconds=0)
        remote = MagicMock()
        remote.execute_command.side_effect = [_remote_ok(stdout="500"), _remote_ok(stdout="200")]
        hc = HealthChecker(cfg, server_config={}, remote_ops=remote)
        with patch("time.sleep"):
            ok, msg = hc.check()
        assert ok is True

    def test_all_retries_fail(self):
        from navig.deploy.health import HealthChecker
        cfg = HealthConfig(url="http://app/health", expected_status=200, retries=2, interval_seconds=0)
        remote = MagicMock()
        remote.execute_command.return_value = _remote_ok(stdout="503")
        hc = HealthChecker(cfg, server_config={}, remote_ops=remote)
        with patch("time.sleep"):
            ok, msg = hc.check()
        assert ok is False
        assert "All 2" in msg


# ---------------------------------------------------------------------------
# navig.deploy.rollback — RollbackManager
# ---------------------------------------------------------------------------

def _make_rm(tmp_path, remote_ops=None, dry_run=False, backup_enabled=True):
    from navig.deploy.rollback import RollbackManager
    return RollbackManager(
        backup_cfg=BackupConfig(enabled=backup_enabled, remote_path="/var/backups", keep_last=3),
        deploy_target="/var/www/myapp",
        app_name="myapp",
        server_config={},
        remote_ops=remote_ops or MagicMock(),
        cache_dir=tmp_path,
        dry_run=dry_run,
    )


class TestRollbackManagerDryRun:
    def test_snapshot_dry_run_returns_record(self, tmp_path):
        rm = _make_rm(tmp_path, dry_run=True)
        record = rm.create_snapshot()
        assert record is not None
        assert "DRY RUN" not in record.path  # path is the snapshot path

    def test_restore_dry_run_returns_true(self, tmp_path):
        rm = _make_rm(tmp_path, dry_run=True)
        snap = SnapshotRecord(path="/var/backups/myapp/20240101_120000", created_at="20240101_120000")
        ok, msg = rm.restore_snapshot(snap)
        assert ok is True
        assert "DRY RUN" in msg


class TestRollbackManagerDisabled:
    def test_snapshot_disabled_returns_none(self, tmp_path):
        rm = _make_rm(tmp_path, backup_enabled=False)
        result = rm.create_snapshot()
        assert result is None


class TestRollbackManagerSaveLoad:
    def test_save_and_load_state(self, tmp_path):
        rm = _make_rm(tmp_path)
        snap = SnapshotRecord(path="/backups/snap", created_at="20240101")
        rm.save_state(snap)
        loaded = rm.load_state()
        assert loaded is not None
        assert loaded.path == "/backups/snap"
        assert loaded.created_at == "20240101"

    def test_load_returns_none_when_no_file(self, tmp_path):
        rm = _make_rm(tmp_path)
        result = rm.load_state()
        assert result is None


class TestRollbackManagerRestore:
    def test_restore_returns_false_when_no_snapshot(self, tmp_path):
        rm = _make_rm(tmp_path)
        ok, msg = rm.restore_snapshot(None)
        assert ok is False
        assert "No snapshot" in msg

    def test_restore_success(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_ok()
        rm = _make_rm(tmp_path, remote_ops=remote)
        snap = SnapshotRecord(path="/snap/path", created_at="ts")
        ok, msg = rm.restore_snapshot(snap)
        assert ok is True

    def test_restore_failure(self, tmp_path):
        remote = MagicMock()
        remote.execute_command.return_value = _remote_fail(stderr="Permission denied")
        rm = _make_rm(tmp_path, remote_ops=remote)
        snap = SnapshotRecord(path="/snap/path", created_at="ts")
        ok, msg = rm.restore_snapshot(snap)
        assert ok is False


# ---------------------------------------------------------------------------
# navig.identity.sigil_store — persist_entity, load_entity, entity_exists, reset_entity
# ---------------------------------------------------------------------------

def _make_entity(seed="abc123", name="Navig", archetype="explorer",
                  palette_key="blue", resonance="high"):
    e = MagicMock()
    e.seed = seed
    e.name = name
    e.archetype = archetype
    e.palette_key = palette_key
    e.resonance = resonance
    return e


class TestPersistAndLoadEntity:
    def test_roundtrip(self, tmp_path):
        entity_path = tmp_path / "entity.json"
        entity = _make_entity()
        with patch("navig.identity.sigil_store._identity_path", return_value=entity_path):
            from navig.identity.sigil_store import persist_entity, load_entity
            persist_entity(entity)
            data = load_entity()
        assert data is not None
        assert data["seed"] == "abc123"
        assert data["name"] == "Navig"

    def test_load_returns_none_when_no_file(self, tmp_path):
        with patch("navig.identity.sigil_store._identity_path", return_value=tmp_path / "missing.json"):
            from navig.identity.sigil_store import load_entity
            assert load_entity() is None

    def test_load_returns_none_on_corrupt_json(self, tmp_path):
        entity_path = tmp_path / "entity.json"
        entity_path.write_text("not json", encoding="utf-8")
        with patch("navig.identity.sigil_store._identity_path", return_value=entity_path):
            from navig.identity.sigil_store import load_entity
            assert load_entity() is None

    def test_load_returns_none_when_seed_missing(self, tmp_path):
        entity_path = tmp_path / "entity.json"
        entity_path.write_text(json.dumps({"name": "X"}), encoding="utf-8")
        with patch("navig.identity.sigil_store._identity_path", return_value=entity_path):
            from navig.identity.sigil_store import load_entity
            assert load_entity() is None


class TestEntityExists:
    def test_true_when_file_exists(self, tmp_path):
        entity_path = tmp_path / "entity.json"
        entity_path.write_text(json.dumps({"seed": "abc", "version": 1}), encoding="utf-8")
        with patch("navig.identity.sigil_store._identity_path", return_value=entity_path):
            from navig.identity.sigil_store import entity_exists
            assert entity_exists() is True

    def test_false_when_no_file(self, tmp_path):
        with patch("navig.identity.sigil_store._identity_path", return_value=tmp_path / "nope.json"):
            from navig.identity.sigil_store import entity_exists
            assert entity_exists() is False


class TestResetEntity:
    def test_deletes_file(self, tmp_path):
        entity_path = tmp_path / "entity.json"
        entity_path.write_text("{}", encoding="utf-8")
        with patch("navig.identity.sigil_store._identity_path", return_value=entity_path):
            from navig.identity.sigil_store import reset_entity
            reset_entity()
        assert not entity_path.exists()

    def test_no_error_when_file_doesnt_exist(self, tmp_path):
        with patch("navig.identity.sigil_store._identity_path", return_value=tmp_path / "nope.json"):
            from navig.identity.sigil_store import reset_entity
            reset_entity()  # should not raise


class TestGetSeedForSession:
    def test_demo_mode_uses_env_var(self):
        with patch.dict("os.environ", {"NAVIG_DEMO_SEED": "myseed123"}):
            from navig.identity.sigil_store import get_seed_for_session
            result = get_seed_for_session(demo=True)
        assert result == "myseed123"

    def test_demo_mode_default_seed(self):
        with patch.dict("os.environ", {}, clear=False):
            env = dict(__import__("os").environ)
            env.pop("NAVIG_DEMO_SEED", None)
            with patch.dict("os.environ", env, clear=True):
                from navig.identity.sigil_store import get_seed_for_session
                result = get_seed_for_session(demo=True)
        assert result == "deadbeef" * 8

    def test_normal_mode_calls_generate_seed(self):
        with patch("navig.identity.seed.generate_seed", return_value="generated"):
            from navig.identity.sigil_store import get_seed_for_session
            result = get_seed_for_session(demo=False)
        assert result == "generated"
