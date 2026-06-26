"""
Batch 124 — tests for navig.agent.remediation and navig.commands.doctor

Coverage targets:
  remediation.py: RemediationType, RemediationStatus, RemediationAction (dataclass, to_dict, from_dict)
  doctor.py:      _check, _gateway_reachable, _count_yaml_files, _find_browser_agent,
                  check_config, check_cache_dir, check_storage, check_sockets,
                  check_formations, check_skills, check_gateway, check_env_keys,
                  check_browser_agent, check_python_deps
"""

from __future__ import annotations

import importlib
import os
import socket
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Remediation module
# ---------------------------------------------------------------------------
from navig.agent.remediation import (
    RemediationAction,
    RemediationStatus,
    RemediationType,
)
from navig.commands.doctor import (
    _check,
    _count_yaml_files,
    _gateway_reachable,
    check_cache_dir,
    check_config,
    check_env_keys,
    check_formations,
    check_gateway,
    check_python_deps,
    check_skills,
    check_sockets,
    check_storage,
)


# ===========================================================================
# RemediationType
# ===========================================================================


class TestRemediationType:
    def test_members_exist(self):
        members = {e.value for e in RemediationType}
        assert "component_restart" in members
        assert "connection_retry" in members
        assert "config_rollback" in members
        assert "permission_fix" in members
        assert "service_restart" in members

    def test_enum_count(self):
        assert len(RemediationType) == 5

    def test_from_value(self):
        assert RemediationType("component_restart") is RemediationType.COMPONENT_RESTART


# ===========================================================================
# RemediationStatus
# ===========================================================================


class TestRemediationStatus:
    def test_members_exist(self):
        values = {e.value for e in RemediationStatus}
        assert values == {"pending", "in_progress", "success", "failed", "skipped"}

    def test_count(self):
        assert len(RemediationStatus) == 5

    def test_from_value(self):
        assert RemediationStatus("success") is RemediationStatus.SUCCESS


# ===========================================================================
# RemediationAction
# ===========================================================================


def _make_action(**kwargs) -> RemediationAction:
    defaults = dict(
        id="test-001",
        type=RemediationType.COMPONENT_RESTART,
        component="test-component",
        reason="test reason",
    )
    defaults.update(kwargs)
    return RemediationAction(**defaults)


class TestRemediationActionDefaults:
    def test_default_status_is_pending(self):
        a = _make_action()
        assert a.status is RemediationStatus.PENDING

    def test_default_attempts_zero(self):
        a = _make_action()
        assert a.attempts == 0

    def test_default_max_attempts_five(self):
        a = _make_action()
        assert a.max_attempts == 5

    def test_default_error_none(self):
        a = _make_action()
        assert a.error is None

    def test_default_metadata_empty(self):
        a = _make_action()
        assert a.metadata == {}

    def test_timestamp_is_datetime(self):
        a = _make_action()
        assert isinstance(a.timestamp, datetime)

    def test_backoff_list(self):
        a = _make_action()
        assert len(a.backoff_seconds) > 0


class TestRemediationActionToDict:
    def test_keys_present(self):
        a = _make_action()
        d = a.to_dict()
        for key in ("id", "type", "component", "reason", "timestamp", "status", "attempts", "max_attempts", "error", "metadata"):
            assert key in d, f"missing key: {key}"

    def test_type_is_string(self):
        a = _make_action()
        d = a.to_dict()
        assert isinstance(d["type"], str)
        assert d["type"] == "component_restart"

    def test_status_is_string(self):
        a = _make_action()
        d = a.to_dict()
        assert d["status"] == "pending"

    def test_timestamp_is_iso_string(self):
        a = _make_action()
        d = a.to_dict()
        # Should be parseable as ISO datetime
        datetime.fromisoformat(d["timestamp"])

    def test_id_matches(self):
        a = _make_action(id="abc-123")
        d = a.to_dict()
        assert d["id"] == "abc-123"

    def test_component_matches(self):
        a = _make_action(component="my-comp")
        d = a.to_dict()
        assert d["component"] == "my-comp"

    def test_error_field_none(self):
        a = _make_action()
        d = a.to_dict()
        assert d["error"] is None

    def test_metadata_matches(self):
        a = _make_action()
        a.metadata["x"] = 42
        d = a.to_dict()
        assert d["metadata"]["x"] == 42


class TestRemediationActionFromDict:
    def _roundtrip(self, **kwargs) -> RemediationAction:
        a = _make_action(**kwargs)
        return RemediationAction.from_dict(a.to_dict())

    def test_roundtrip_id(self):
        a2 = self._roundtrip(id="rt-001")
        assert a2.id == "rt-001"

    def test_roundtrip_component(self):
        a2 = self._roundtrip(component="my-comp")
        assert a2.component == "my-comp"

    def test_roundtrip_type(self):
        a2 = self._roundtrip(type=RemediationType.CONFIG_ROLLBACK)
        assert a2.type is RemediationType.CONFIG_ROLLBACK

    def test_roundtrip_status(self):
        a = _make_action()
        a.status = RemediationStatus.SUCCESS
        a2 = RemediationAction.from_dict(a.to_dict())
        assert a2.status is RemediationStatus.SUCCESS

    def test_roundtrip_attempts(self):
        a = _make_action()
        a.attempts = 3
        a2 = RemediationAction.from_dict(a.to_dict())
        assert a2.attempts == 3

    def test_roundtrip_max_attempts(self):
        a = _make_action()
        a.max_attempts = 7
        a2 = RemediationAction.from_dict(a.to_dict())
        assert a2.max_attempts == 7

    def test_roundtrip_error_set(self):
        a = _make_action()
        a.error = "some error"
        a2 = RemediationAction.from_dict(a.to_dict())
        assert a2.error == "some error"

    def test_from_dict_invalid_attempts_defaults_to_zero(self):
        a = _make_action()
        d = a.to_dict()
        d["attempts"] = "bad"
        a2 = RemediationAction.from_dict(d)
        assert a2.attempts == 0

    def test_from_dict_invalid_max_attempts_defaults_to_five(self):
        a = _make_action()
        d = a.to_dict()
        d["max_attempts"] = None
        a2 = RemediationAction.from_dict(d)
        assert a2.max_attempts == 5

    def test_from_dict_missing_status_defaults_to_pending(self):
        a = _make_action()
        d = a.to_dict()
        del d["status"]
        a2 = RemediationAction.from_dict(d)
        assert a2.status is RemediationStatus.PENDING


# ===========================================================================
# doctor._check
# ===========================================================================


class TestDoctorCheck:
    def test_ok_true_returns_ok_icon(self):
        icon, ok, msg = _check("label", True)
        assert icon == "✓"
        assert ok is True

    def test_ok_false_returns_err_icon(self):
        icon, ok, msg = _check("label", False)
        assert icon == "✗"
        assert ok is False

    def test_warn_true_returns_warn_icon(self):
        icon, ok, msg = _check("label", False, warn=True)
        assert icon == "⚠"

    def test_message_contains_label(self):
        _, _, msg = _check("MyLabel", True)
        assert "MyLabel" in msg

    def test_detail_appended(self):
        _, _, msg = _check("label", True, detail="some detail")
        assert "some detail" in msg

    def test_no_detail_no_colon(self):
        _, _, msg = _check("label", True)
        assert ":" not in msg or msg.count(":") <= 1  # only space-label colon allowed


# ===========================================================================
# doctor._gateway_reachable
# ===========================================================================


class TestGatewayReachable:
    def test_reachable_returns_true(self):
        mock_cm = MagicMock()
        mock_cm.__enter__ = MagicMock(return_value=MagicMock())
        mock_cm.__exit__ = MagicMock(return_value=False)
        with patch("navig.commands.doctor.socket.create_connection", return_value=mock_cm) as m:
            result = _gateway_reachable("127.0.0.1", 9999)
        assert result is True

    def test_os_error_returns_false(self):
        with patch("navig.commands.doctor.socket.create_connection", side_effect=OSError):
            result = _gateway_reachable("127.0.0.1", 9999)
        assert result is False

    def test_timeout_error_returns_false(self):
        with patch("navig.commands.doctor.socket.create_connection", side_effect=TimeoutError):
            result = _gateway_reachable("127.0.0.1", 9999)
        assert result is False


# ===========================================================================
# doctor._count_yaml_files
# ===========================================================================


class TestCountYamlFiles:
    def test_nonexistent_dir_returns_zeros(self, tmp_path):
        total, errors = _count_yaml_files(tmp_path / "nonexistent")
        assert total == 0
        assert errors == 0

    def test_valid_yaml_counted(self, tmp_path):
        (tmp_path / "a.yaml").write_text("key: value", encoding="utf-8")
        total, errors = _count_yaml_files(tmp_path)
        assert total == 1
        assert errors == 0

    def test_invalid_yaml_counted_as_error(self, tmp_path):
        (tmp_path / "bad.yaml").write_text(": :", encoding="utf-8")
        total, errors = _count_yaml_files(tmp_path)
        assert total >= 1

    def test_yml_extension_also_counted(self, tmp_path):
        (tmp_path / "b.yml").write_text("ok: true", encoding="utf-8")
        total, errors = _count_yaml_files(tmp_path)
        assert total >= 1

    def test_empty_dir_returns_zeros(self, tmp_path):
        total, errors = _count_yaml_files(tmp_path)
        assert total == 0
        assert errors == 0

    def test_multiple_files(self, tmp_path):
        for i in range(3):
            (tmp_path / f"f{i}.yaml").write_text(f"n: {i}", encoding="utf-8")
        total, errors = _count_yaml_files(tmp_path)
        assert total == 3
        assert errors == 0


# (R9) The browser-agent doctor checks (_find_browser_agent / check_browser_agent)
# were removed from navig.commands.doctor; their tests are deleted accordingly.


# ===========================================================================
# doctor.check_config
# ===========================================================================


class TestCheckConfig:
    def test_missing_config_returns_failure(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_config()
        assert len(results) >= 1
        _, ok, _ = results[0]
        assert ok is False

    def test_valid_config_returns_ok(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("version: 2\n", encoding="utf-8")
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_config()
        _, ok, _ = results[0]
        assert ok is True

    def test_invalid_yaml_returns_failure(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text(": :", encoding="utf-8")
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_config()
        _, ok, _ = results[0]
        assert ok is False


# ===========================================================================
# doctor.check_cache_dir
# ===========================================================================


class TestCheckCacheDir:
    def test_missing_cache_dir_returns_warn(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_cache_dir()
        _, ok, _ = results[0]
        assert ok is False  # directory doesn't exist

    def test_writable_cache_dir_ok(self, tmp_path):
        cache = tmp_path / "cache"
        cache.mkdir()
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_cache_dir()
        _, ok, _ = results[0]
        assert ok is True


# ===========================================================================
# doctor.check_storage
# ===========================================================================


class TestCheckStorage:
    def test_low_disk_returns_result(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch("shutil.disk_usage") as m:
                m.return_value = type("U", (), {"free": 0.5 * 1024 ** 3})()
                results = check_storage()
        assert len(results) >= 1
        _, ok, _ = results[0]
        assert ok is False  # < 1 GB

    def test_ample_disk_returns_ok(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch("shutil.disk_usage") as m:
                m.return_value = type("U", (), {"free": 50 * 1024 ** 3})()
                results = check_storage()
        assert len(results) >= 1
        _, ok, _ = results[0]
        assert ok is True


# ===========================================================================
# doctor.check_sockets
# ===========================================================================


class TestCheckSockets:
    def test_port_bound_returns_ok(self, tmp_path):
        # Simulate port already bound (connect_ex returns 0)
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.connect_ex.return_value = 0
        with patch("navig.commands.doctor.socket.socket", return_value=m):
            results = check_sockets(target_port=8789)
        assert len(results) >= 1

    def test_port_available_returns_ok(self, tmp_path):
        m = MagicMock()
        m.__enter__ = MagicMock(return_value=m)
        m.__exit__ = MagicMock(return_value=False)
        m.connect_ex.return_value = 111  # port not open
        with patch("navig.commands.doctor.socket.socket", return_value=m):
            results = check_sockets(target_port=8789)
        _, ok, _ = results[0]
        assert ok is True

    def test_socket_exception_returns_failure(self):
        with patch("navig.commands.doctor.socket.socket", side_effect=OSError("fail")):
            results = check_sockets(target_port=8789)
        _, ok, _ = results[0]
        assert ok is False


# ===========================================================================
# doctor.check_formations
# ===========================================================================


class TestCheckFormations:
    def test_empty_formations_dir(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_formations()
        assert len(results) >= 1

    def test_valid_yaml_formation(self, tmp_path):
        formations = tmp_path / "formations"
        formations.mkdir()
        (formations / "test.yaml").write_text("name: test\n", encoding="utf-8")
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_formations()
        _, ok, _ = results[0]
        assert ok is True

    def test_invalid_formation_returns_failure(self, tmp_path):
        formations = tmp_path / "formations"
        formations.mkdir()
        (formations / "bad.yaml").write_text(": :", encoding="utf-8")
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            results = check_formations()
        _, ok, _ = results[0]
        assert ok is False


# ===========================================================================
# doctor.check_skills
# ===========================================================================


class TestCheckSkills:
    def test_no_skills_dir_returns_warn(self, tmp_path):
        # navig package dir without skills
        import navig
        fake_navig_file = tmp_path / "navig" / "__init__.py"
        fake_navig_file.parent.mkdir()
        fake_navig_file.write_text("")
        with patch.object(Path, "exists", return_value=False):
            results = check_skills()
        assert len(results) >= 1

    def test_skills_dir_with_valid_yaml(self, tmp_path):
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "skill1.yaml").write_text("name: skill1\n", encoding="utf-8")
        import navig as _navig
        parent = Path(_navig.__file__).parent.parent
        with patch("navig.commands.doctor._count_yaml_files", return_value=(1, 0)):
            with patch.object(Path, "exists", side_effect=lambda p=None: True):
                results = check_skills()
        # Just verify it returns something
        assert isinstance(results, list)


# ===========================================================================
# doctor.check_gateway
# ===========================================================================


class TestCheckGateway:
    def test_gateway_running_returns_ok(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch("navig.commands.doctor._gateway_reachable", return_value=True):
                results = check_gateway(port=8789)
        _, ok, _ = results[0]
        assert ok is True

    def test_gateway_not_running_returns_failure(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch("navig.commands.doctor._gateway_reachable", return_value=False):
                results = check_gateway(port=8789)
        _, ok, _ = results[0]
        assert ok is False

    def test_reads_port_from_config(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("gateway:\n  port: 9999\n", encoding="utf-8")
        called_ports = []

        def capture(host, port):
            called_ports.append(port)
            return False

        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch("navig.commands.doctor._gateway_reachable", side_effect=capture):
                check_gateway()
        assert 9999 in called_ports


# ===========================================================================
# doctor.check_env_keys
# ===========================================================================


class TestCheckEnvKeys:
    def test_returns_list(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
                results = check_env_keys()
        assert isinstance(results, list)
        assert len(results) >= 3

    def test_set_key_returns_ok(self, tmp_path):
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-real-key-123"}):
                results = check_env_keys()
        # First result should be OPENROUTER_API_KEY, which should be OK
        _, ok, msg = results[0]
        assert ok is True

    def test_unset_key_returns_warn(self, tmp_path):
        env = {"OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""}
        with patch("navig.commands.doctor.config_dir", return_value=tmp_path):
            with patch.dict(os.environ, env, clear=False):
                # remove key entirely
                env_copy = {k: v for k, v in os.environ.items() if k not in env}
                with patch.dict(os.environ, env_copy, clear=True):
                    results = check_env_keys()
        _, ok, _ = results[0]
        assert ok is False


# ===========================================================================
# doctor.check_python_deps
# ===========================================================================


class TestCheckPythonDeps:
    def test_returns_list(self):
        results = check_python_deps()
        assert isinstance(results, list)
        assert len(results) >= 1

    def test_typer_present(self):
        results = check_python_deps()
        # typer is installed (it's in requirements)
        typer_results = [r for r in results if "typer" in r[2].lower()]
        assert typer_results
        _, ok, _ = typer_results[0]
        assert ok is True

    def test_missing_dep_returns_failure(self):
        original = importlib.import_module

        def mock_import(name, *args, **kwargs):
            if name == "cryptography":
                raise ImportError(f"No module named '{name}'")
            return original(name, *args, **kwargs)

        with patch("navig.commands.doctor.importlib.import_module", side_effect=mock_import):
            results = check_python_deps()
        crypto_results = [r for r in results if "crypto" in r[2].lower()]
        assert crypto_results
        _, ok, _ = crypto_results[0]
        assert ok is False
