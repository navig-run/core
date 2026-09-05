"""`navig database` reported every failure and exited 0 — including a lost safety backup.

The worst path in this module is not an exit code at all. `restore_database` takes a
safety backup of the live database before overwriting it, and printed

    ✓ Safety backup created: prod_pre_restore_20260730.sql

**unconditionally** — while `backup_database` returned normally after a failed
mysqldump. So a failed safety backup announced itself as created, and the restore
then replaced the database with no rollback in existence. Every other bug in this
file misreports; that one destroys data.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer

import navig.commands.database as db_mod

pytestmark = pytest.mark.integration


def _wire(monkeypatch, tmp_path, *, db_name="appdb"):
    """Minimal server/tunnel wiring; no network, no mysql binary."""
    cfg = SimpleNamespace(
        backups_dir=tmp_path / "backups",
        load_server_config=lambda _n: {
            "database": {
                "name": db_name,
                "user": "u",
                "password": "p",
                "direct_host": "127.0.0.1",
            }
        },
    )
    (tmp_path / "backups").mkdir(exist_ok=True)

    monkeypatch.setitem(
        __import__("sys").modules, "navig.config",
        SimpleNamespace(get_config_manager=lambda: cfg),
    )
    monkeypatch.setitem(
        __import__("sys").modules, "navig.tunnel",
        SimpleNamespace(TunnelManager=lambda _c: SimpleNamespace(
            get_tunnel_status=lambda _n: None, start_tunnel=lambda _n: None)),
    )
    monkeypatch.setitem(
        __import__("sys").modules, "navig.cli.recovery",
        SimpleNamespace(require_active_server=lambda *_a, **_k: "prod"),
    )
    monkeypatch.setattr(db_mod, "get_db_host_port", lambda *_a: ("127.0.0.1", 3306))
    monkeypatch.setattr(db_mod, "create_mysql_config_file", lambda *_a: str(tmp_path / "my.cnf"))
    (tmp_path / "my.cnf").write_text("x", encoding="utf-8")
    # `execute_sql` gates on ch.confirm_operation (which reads the execution mode
    # off the real config manager). Approve it here — the gate is another module's
    # contract, and these tests are about what happens AFTER the query runs.
    monkeypatch.setattr(db_mod.ch, "confirm_operation", lambda *_a, **_k: True)
    return cfg


# ── SQL ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("json_mode", [False, True])
def test_a_failed_sql_statement_exits_non_zero(monkeypatch, tmp_path, json_mode):
    """`navig db query "UPDATE …" && <next>` must not run <next> on a failure.

    Parametrised over --json deliberately: the JSON payload already reported
    `"success": false` while the process reported 0, so the two disagreed.
    """
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(db_mod, "run_mysql_query", lambda *_a: (False, "", "syntax error"))

    with pytest.raises(typer.Exit) as exc:
        db_mod.execute_sql("UPDATE t SET x=1", {"json": json_mode})
    assert exc.value.exit_code == 1


def test_a_successful_sql_statement_still_exits_zero(monkeypatch, tmp_path):
    """Anti-vacuity — otherwise raising unconditionally would pass the test above."""
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(db_mod, "run_mysql_query", lambda *_a: (True, "1 row", ""))

    db_mod.execute_sql("SELECT 1", {})  # must not raise


def test_a_missing_sql_file_is_a_usage_error(tmp_path):
    with pytest.raises(typer.Exit) as exc:
        db_mod.execute_sql_file(tmp_path / "nope.sql", {})
    assert exc.value.exit_code == 2, "bad input is exit 2, not a runtime failure"


# ── backup ─────────────────────────────────────────────────────────────


def test_a_failed_mysqldump_exits_non_zero(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stderr="access denied"),
    )

    with pytest.raises(typer.Exit) as exc:
        db_mod.backup_database(tmp_path / "out.sql", {})
    assert exc.value.exit_code == 1


def test_a_missing_mysqldump_exits_non_zero(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)

    def _absent(*_a, **_k):
        raise FileNotFoundError("mysqldump")

    monkeypatch.setattr(subprocess, "run", _absent)

    with pytest.raises(typer.Exit) as exc:
        db_mod.backup_database(tmp_path / "out.sql", {})
    assert exc.value.exit_code == 1


def test_a_successful_backup_still_exits_zero(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stderr="")
    )
    db_mod.backup_database(tmp_path / "out.sql", {})  # must not raise


# ── restore: the destructive one ────────────────────────────────────────


def _dump(tmp_path, name="backup.sql") -> Path:
    f = tmp_path / name
    f.write_text("-- SQL dump\n", encoding="utf-8")
    return f


def test_a_missing_restore_file_is_a_usage_error(tmp_path):
    with pytest.raises(typer.Exit) as exc:
        db_mod.restore_database(tmp_path / "nope.sql", {"yes": True})
    assert exc.value.exit_code == 2


def test_a_declined_confirmation_exits_non_zero(monkeypatch, tmp_path, capsys):
    """The --json payload already said `"success": false, "cancelled": true`."""
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr("builtins.input", lambda *_a: "no")

    with pytest.raises(typer.Exit) as exc:
        db_mod.restore_database(_dump(tmp_path), {})
    assert exc.value.exit_code == 1
    assert "cancelled" in capsys.readouterr().out.lower()


def test_a_refused_restore_over_a_corrupt_backup_exits_non_zero(monkeypatch, tmp_path):
    """It REFUSED to restore, then reported success.

    A recovery script would move on believing the database had been restored from
    a backup this command had just declined to use.
    """
    import json as _json

    _wire(monkeypatch, tmp_path)
    f = _dump(tmp_path)
    (tmp_path / "metadata.json").write_text(
        _json.dumps({"databases": [{"file": f.name, "checksum": "0" * 64}]}), encoding="utf-8"
    )
    monkeypatch.setattr(db_mod, "calculate_file_checksum", lambda _f: "f" * 64)

    with pytest.raises(typer.Exit) as exc:
        db_mod.restore_database(f, {"yes": True})
    assert exc.value.exit_code == 1


def test_a_failed_restore_exits_non_zero(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path)
    calls: list[str] = []

    def _run(cmd, *_a, **_k):
        calls.append(cmd[0])
        # the safety backup (mysqldump) succeeds; the restore (mysql) fails
        return SimpleNamespace(returncode=0 if cmd[0] == "mysqldump" else 1, stderr="boom")

    monkeypatch.setattr(subprocess, "run", _run)

    with pytest.raises(typer.Exit) as exc:
        db_mod.restore_database(_dump(tmp_path), {"yes": True})
    assert exc.value.exit_code == 1
    assert "mysql" in calls, "the restore was never attempted"


def test_a_failed_safety_backup_refuses_to_restore(monkeypatch, tmp_path, capsys):
    """THE DATA-LOSS PATH: no rollback available, so do not overwrite.

    `✓ Safety backup created` printed unconditionally while `backup_database`
    returned normally after a failed mysqldump — so the restore proceeded to
    replace the database with no recoverable copy in existence.
    """
    _wire(monkeypatch, tmp_path)
    attempted: list[str] = []

    def _run(cmd, *_a, **_k):
        attempted.append(cmd[0])
        # mysqldump (the safety backup) FAILS; mysql (the restore) would succeed
        return SimpleNamespace(returncode=1 if cmd[0] == "mysqldump" else 0, stderr="disk full")

    monkeypatch.setattr(subprocess, "run", _run)

    with pytest.raises(typer.Exit) as exc:
        db_mod.restore_database(_dump(tmp_path), {"yes": True})

    assert exc.value.exit_code == 1
    out = capsys.readouterr().out
    assert "Refusing to restore" in out, out
    assert "no rollback" in out
    # The load-bearing assertion: the destructive command never ran.
    assert "mysql" not in attempted, (
        f"the database was overwritten with no safety backup: {attempted}"
    )
    # And it must not have claimed the backup exists.
    assert "Safety backup created" not in out


def test_a_successful_restore_still_exits_zero(monkeypatch, tmp_path):
    """Anti-vacuity for the whole restore path."""
    _wire(monkeypatch, tmp_path)
    monkeypatch.setattr(
        subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stderr="")
    )
    db_mod.restore_database(_dump(tmp_path), {"yes": True})  # must not raise
