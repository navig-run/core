from types import SimpleNamespace

import pytest
import typer

from navig.commands.database_advanced import _validate_sql_identifier

pytestmark = pytest.mark.integration


def test_validate_sql_identifier_allows_legitimate_substring_or():
    assert _validate_sql_identifier("orders", "table") is True


def test_validate_sql_identifier_rejects_exact_reserved_keyword():
    with pytest.raises(ValueError, match="reserved SQL keyword"):
        _validate_sql_identifier("drop", "table")


def test_validate_sql_identifier_still_rejects_invalid_chars():
    with pytest.raises(ValueError, match="Only alphanumeric characters and underscores"):
        _validate_sql_identifier("users;", "table")


# ── exit honesty ─────────────────────────────────────────────────────────────
# `list_users_cmd` returns NOTHING, so neither caller (`navig db show --users`, the
# deprecated `navig db users`) can inspect a result — printing "Query failed" and
# returning reported success for a query that never ran. Its siblings optimize/repair
# DO return a bool and their wrappers check it (#752); a function that returns nothing
# has to raise.


@pytest.fixture
def dba(monkeypatch, tmp_path):
    """Stub everything the command touches BEFORE the query.

    `list_users_cmd` resolves an active server (which can PROMPT) and constructs a
    TunnelManager UNCONDITIONALLY — before the `direct_host` check that would make the
    tunnel unnecessary — so stubbing the class is required, not optional. An
    under-stubbed version of this fixture hung until the timeout, then failed on
    `config_manager.tunnels_file`, then on `.log_file`: chasing those attributes one at
    a time is the wrong move when the whole collaborator is irrelevant here.
    """
    import navig.cli.recovery as recovery
    import navig.commands.database_advanced as mod
    import navig.config as config_mod
    import navig.tunnel as tunnel_mod

    server_config = {
        "database": {"direct_host": "127.0.0.1", "user": "root", "password": "secret"}
    }
    monkeypatch.setattr(recovery, "require_active_server", lambda *a, **k: "prod")
    monkeypatch.setattr(
        config_mod,
        "get_config_manager",
        lambda: SimpleNamespace(load_server_config=lambda _n: server_config),
    )
    monkeypatch.setattr(
        tunnel_mod,
        "TunnelManager",
        lambda *a, **k: SimpleNamespace(
            get_tunnel_status=lambda *a, **k: None,
            start_tunnel=lambda *a, **k: None,
        ),
    )
    monkeypatch.setattr(mod, "get_db_host_port", lambda *a, **k: ("127.0.0.1", 3306))
    monkeypatch.setattr(
        mod, "create_mysql_config_file", lambda *a, **k: str(tmp_path / "my.cnf")
    )
    return mod


def _result(returncode: int, stdout: str = "", stderr: str = ""):
    return lambda *a, **k: SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_list_users_query_failure_exits_nonzero(monkeypatch, dba):
    monkeypatch.setattr(dba.subprocess, "run", _result(1, stderr="ERROR 1045: Access denied"))

    with pytest.raises(typer.Exit) as exc:
        dba.list_users_cmd({"quiet": True})

    assert exc.value.exit_code == 1


def test_list_users_empty_result_is_not_a_failure(monkeypatch, dba):
    """Anti-vacuity, and a real product rule: no users is EMPTY, not broken. It warns
    and exits 0 — turning that into a non-zero exit would be the opposite bug."""
    monkeypatch.setattr(dba.subprocess, "run", _result(0, stdout="User"))

    dba.list_users_cmd({"quiet": True})  # must NOT raise


def test_the_dead_duplicates_are_gone():
    """`list_databases_cmd` / `list_tables_cmd` were unreachable duplicates of
    `navig db list` / `navig db tables` — no importer, not in the shipped manifest, no
    dynamic dispatch, no tests — and carried 3 of this module's 4 exit-0 paths. If
    either comes back it needs a caller and a manifest entry, not a revival."""
    import navig.commands.database_advanced as mod

    assert not hasattr(mod, "list_databases_cmd")
    assert not hasattr(mod, "list_tables_cmd")
