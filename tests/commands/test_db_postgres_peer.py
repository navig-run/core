"""A host can run MariaDB AND PostgreSQL; `navig db query -d <pg database>` must reach
PostgreSQL, with a PostgreSQL login.

Found on cybesis-vps (2026-10-03): the host declares `database.type: mariadb`, so every
`navig db query "…" -d schema_core` went to MariaDB with MariaDB's root password
("ERROR 1045 Access denied"); with `-t postgresql` it ran `psql -U postgres` as root and
stock peer auth refused it. These pin the three fixes: the named database picks its
engine, a login stays with its engine, and the postgres superuser logs in AS postgres.
"""

from __future__ import annotations

import shlex
from unittest.mock import MagicMock

import navig.commands.db as db_mod

# ── the command line ─────────────────────────────────────────────────────────


def test_postgres_superuser_without_password_runs_as_postgres():
    cmd, _ = db_mod._build_db_command("postgresql", "SELECT 1", "postgres", None, "schema_core")
    assert cmd == "cd /tmp && sudo -n -u postgres psql -U postgres -d schema_core -c 'SELECT 1'"


def test_postgres_with_password_keeps_pgpassword_and_no_sudo():
    cmd, stdin = db_mod._build_db_command("postgresql", "SELECT 1", "app", "s3cret", "db1")
    # The password travels on stdin into PGPASSWORD — never in the command line.
    assert "psql -U app" in cmd and "s3cret" not in cmd
    assert stdin == "s3cret\n"
    assert "sudo" not in cmd


def test_postgres_other_role_without_password_is_unchanged():
    # Only the superuser is assumed to have a matching OS account.
    cmd, _ = db_mod._build_db_command("postgresql", "SELECT 1", "app", None, "db1")
    assert cmd == "psql -U app -d db1 -c 'SELECT 1'"


def test_postgres_in_a_container_is_not_sudo_wrapped():
    cmd, _ = db_mod._build_db_command("postgresql", "SELECT 1", "postgres", None, "db1", "pg")
    assert "sudo" not in cmd
    assert cmd.startswith("docker exec pg sh -c")


def test_mysql_password_rides_stdin_not_the_command_line():
    cmd, stdin = db_mod._build_db_command("mariadb", "SELECT 1", "root", "pw", "site")
    assert "mariadb -uroot site -e" in cmd and "-ppw" not in cmd
    assert stdin == "pw\n"


# ── the login belongs to its engine ──────────────────────────────────────────


def _config(host_db: dict, app_db: dict | None = None):
    cm = MagicMock()
    cm.get_active_app.return_value = "app1" if app_db is not None else None
    cm.load_app_config.return_value = {"database": app_db or {}}
    cm.load_host_config.return_value = {"database": host_db}
    return cm


MARIADB_HOST = {"type": "mariadb", "root_user": "root", "root_password": "maria-pw"}


def test_mariadb_root_login_never_reaches_postgres():
    user, password, _ = db_mod._get_db_credentials_from_config(
        _config(MARIADB_HOST), "h", None, None, "postgresql"
    )
    assert (user, password) == ("postgres", None)


def test_mariadb_root_login_still_serves_mariadb():
    user, password, db_type = db_mod._get_db_credentials_from_config(
        _config(MARIADB_HOST), "h", None, None, None
    )
    assert (user, password, db_type) == ("root", "maria-pw", "mariadb")


def test_mysql_and_mariadb_share_logins():
    user, password, _ = db_mod._get_db_credentials_from_config(
        _config(MARIADB_HOST), "h", None, None, "mysql"
    )
    assert (user, password) == ("root", "maria-pw")


def test_an_app_postgres_login_is_used_for_postgres():
    cm = _config(MARIADB_HOST, {"type": "postgresql", "user": "schema_api", "password": "pg-pw"})
    user, password, _ = db_mod._get_db_credentials_from_config(cm, "h", None, None, "postgresql")
    assert (user, password) == ("schema_api", "pg-pw")


# ── the named database picks its engine ──────────────────────────────────────


def test_probe_reads_one_as_present_and_quotes_the_name():
    disco = MagicMock()
    disco._execute_ssh.return_value = (True, "1\n", "")
    assert db_mod._postgres_has_database(disco, "o'brien") is True
    sent = disco._execute_ssh.call_args[0][0]
    assert "sudo -n -u postgres psql -Atqc" in sent
    # What psql receives, after the shell unquotes it: the name SQL-escaped.
    argv = shlex.split(sent.split("&&")[-1])
    assert argv[argv.index("-Atqc") + 1] == "SELECT 1 FROM pg_database WHERE datname = 'o''brien'"


def test_probe_failure_or_absence_reads_false():
    disco = MagicMock()
    disco._execute_ssh.return_value = (True, "", "")
    assert db_mod._postgres_has_database(disco, "site") is False
    disco._execute_ssh.return_value = (False, "", "sudo: a password is required")
    assert db_mod._postgres_has_database(disco, "site") is False


def _run_query(monkeypatch, has_pg: bool, db_type=None):
    disco = MagicMock()
    sent: list[str] = []

    def ssh(cmd, stdin=None):
        sent.append(cmd)
        if "pg_database" in cmd:
            return (True, "1\n" if has_pg else "", "")
        return (True, "?column?\n1\n", "")

    disco._execute_ssh.side_effect = ssh
    monkeypatch.setattr(db_mod, "_resolve_host_discovery", lambda opts: ("h", _config(MARIADB_HOST), disco))
    monkeypatch.setattr(db_mod.ch, "format_db_output_plain", lambda out: None)
    db_mod.db_query_cmd(
        query="SELECT 1", container=None, user="root", password=None,
        database="schema_core", db_type=db_type, options={"plain": True},
    )
    return sent[-1]


def test_a_postgres_database_on_a_mariadb_host_goes_to_postgres(monkeypatch):
    final = _run_query(monkeypatch, has_pg=True)
    assert final == "cd /tmp && sudo -n -u postgres psql -U postgres -d schema_core -c 'SELECT 1'"


def test_a_mariadb_database_stays_on_mariadb(monkeypatch):
    final = _run_query(monkeypatch, has_pg=False)
    assert "mariadb -uroot schema_core" in final and "maria-pw" not in final


def test_an_explicit_type_skips_the_probe(monkeypatch):
    final = _run_query(monkeypatch, has_pg=True, db_type="mariadb")
    assert "mariadb -uroot" in final
