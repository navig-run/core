"""db_dump_cmd must key backup success on the command's EXIT CODE, not on whether it
produced output. A valid dump of an empty database is (near-)empty, so `success and
stdout` misreported a legitimate empty backup as "Backup failed" (typer.Exit(1)).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import typer

import navig.commands.db as db_mod


def _run(monkeypatch, tmp_path, ssh_result):
    disco = MagicMock()
    disco._execute_ssh.return_value = ssh_result
    monkeypatch.setattr(db_mod, "_resolve_host_discovery",
                        lambda opts: ("host1", MagicMock(), disco))
    monkeypatch.setattr(db_mod, "_get_db_credentials_from_config",
                        lambda *a: ("root", None, "mysql"))
    out = tmp_path / "backup.sql"
    db_mod.db_dump_cmd(database="mydb", output=out, container=None, user="root",
                       password=None, db_type="mysql", options={})
    return out


def test_empty_but_successful_dump_is_saved_not_failed(monkeypatch, tmp_path):
    # success=True with empty stdout is a valid empty backup — not a failure.
    out = _run(monkeypatch, tmp_path, (True, "", ""))
    assert out.exists()            # written, no typer.Exit raised (old code raised Exit(1))
    assert out.read_text() == ""   # the empty dump is preserved


def test_successful_dump_writes_content(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, (True, "-- MySQL dump\nCREATE TABLE t (id INT);\n", ""))
    assert "CREATE TABLE" in out.read_text()


def test_failed_dump_raises_exit(monkeypatch, tmp_path):
    with pytest.raises(typer.Exit) as exc:
        _run(monkeypatch, tmp_path, (False, "", "Access denied"))
    assert exc.value.exit_code == 1
