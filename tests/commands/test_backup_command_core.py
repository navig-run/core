from __future__ import annotations

import subprocess
from types import SimpleNamespace

import navig.commands.backup as backup_mod
import pytest

pytestmark = pytest.mark.integration


def test_result_indicates_missing_uses_stdout_text():
    missing = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="missing\n", stderr="")
    exists = subprocess.CompletedProcess(args=["x"], returncode=0, stdout="exists\n", stderr="")

    assert backup_mod._result_indicates_missing(missing) is True
    assert backup_mod._result_indicates_missing(exists) is False


def test_result_stdout_text_handles_non_process_objects():
    class _NoStdout:
        pass

    assert backup_mod._result_stdout_text(_NoStdout()) == ""


def test_backup_system_config_skips_missing_files_without_type_error(monkeypatch, tmp_path):
    calls: dict[str, int] = {"scp": 0}

    cfg_mgr = SimpleNamespace(
        backups_dir=tmp_path / "backups",
        load_server_config=lambda _name: {
            "ssh_key": "~/.ssh/id_rsa",
            "user": "root",
            "host": "example.com",
        },
    )

    class _Remote:
        def __init__(self, _cfg):
            pass

        def execute_command(self, _cmd):
            return subprocess.CompletedProcess(args=["x"], returncode=0, stdout="missing\n", stderr="")

    monkeypatch.setitem(
        __import__("sys").modules,
        "navig.config",
        SimpleNamespace(get_config_manager=lambda: cfg_mgr),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "navig.remote",
        SimpleNamespace(RemoteOperations=_Remote),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "navig.cli.recovery",
        SimpleNamespace(require_active_server=lambda *_a, **_kw: "prod"),
    )

    monkeypatch.setattr(
        backup_mod,
        "_run_scp_command",
        lambda *_a, **_kw: calls.__setitem__("scp", calls["scp"] + 1),
    )

    backup_mod.backup_system_config(name="unit", options={})

    assert calls["scp"] == 0
