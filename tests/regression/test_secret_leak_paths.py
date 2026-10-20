"""Four paths that put a real credential where it must never be (found 2026-10-15).

1. ``navig backup export`` (archive) copied ``<host>.yaml.bak.<stamp>`` — the
   PLAINTEXT original `migrate-host-secrets --apply` keeps — into an export
   labelled redacted. Redaction only matched ``*.yaml``.
2. The JSON export resolved vault refs back into plaintext and masked only
   ``database.password``: ``ssh_password`` and ``database.root_password`` shipped.
3. Database commands carried the password in the SSH command line
   (``mysql -p'<pw>'``, ``PGPASSWORD=<pw> psql``): local argv, navig's debug log,
   the deck's policy/audit record and the server's ``ps``.
4. The log redactor only knew ``-p <value>`` with a space, never ``-p<value>``.
"""

from __future__ import annotations

import json
import tarfile
import tempfile
from pathlib import Path

import pytest
import yaml

from navig.commands import config_backup
from navig.commands import db as db_mod
from navig.core.remote_secret_env import with_secret_env
from navig.core.security import redact_sensitive_text

SECRET = "Pa55'w\"rd$(id)"


# ── 1. archive export ───────────────────────────────────────────────────────


class _CM:
    def __init__(self, root: Path) -> None:
        self.config_dir = root

    def get_config_directories(self) -> list[Path]:
        return [self.config_dir]


def _archive_members(monkeypatch: pytest.MonkeyPatch, root: Path) -> dict[str, bytes]:
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _CM(root))
    out = Path(tempfile.mkdtemp()) / "cfg.tar.gz"
    config_backup._create_archive(out)
    members: dict[str, bytes] = {}
    with tarfile.open(out) as tar:
        for m in tar.getmembers():
            if m.isfile():
                members[m.name] = tar.extractfile(m).read()
    return members


def test_archive_never_ships_a_migration_backup(monkeypatch, tmp_path):
    hosts = tmp_path / "hosts"
    hosts.mkdir()
    (hosts / "prod.yaml").write_text(
        "host: 203.0.113.1\nssh_password_ref: vault:host/prod/ssh_password\n", encoding="utf-8"
    )
    (hosts / "prod.yaml.bak.20261015_120000_000001").write_text(
        yaml.safe_dump({"host": "203.0.113.1", "ssh_password": SECRET}), encoding="utf-8"
    )

    members = _archive_members(monkeypatch, tmp_path)

    assert not any(".bak." in name for name in members), sorted(members)
    for name, body in members.items():
        assert SECRET.encode() not in body, f"{name} carries the plaintext"
    manifest = json.loads(members["navig-config/manifest.json"])
    assert manifest["skipped_backups"] == ["hosts/prod.yaml.bak.20261015_120000_000001"]
    assert manifest["counts"]["hosts"] == 1


def test_redaction_drops_any_file_it_cannot_parse_as_yaml(tmp_path):
    """Not just `.bak` — a `.yml`, `.env` or stray file is redacted or dropped, never copied raw."""
    (tmp_path / "hosts").mkdir()
    (tmp_path / "hosts" / "a.yml").write_text(f"ssh_password: {json.dumps(SECRET)}\n", encoding="utf-8")
    (tmp_path / "hosts" / "notes.env").write_text(f"DB_PASSWORD={SECRET}\n", encoding="utf-8")

    dropped = config_backup._redact_secrets_in_dir(tmp_path)

    assert dropped == ["hosts/notes.env"]
    assert SECRET not in (tmp_path / "hosts" / "a.yml").read_text(encoding="utf-8")


# ── 2. JSON export ──────────────────────────────────────────────────────────


def test_json_export_masks_every_resolved_host_credential(monkeypatch):
    resolved = {
        "host": "203.0.113.1",
        "ssh_password": SECRET,
        "ssh_password_ref": "vault:host/prod/ssh_password",
        "database": {"root_password": SECRET, "password": SECRET, "user": "app"},
    }

    class CM:
        global_config: dict = {}

        def list_hosts(self):
            return ["prod"]

        def load_host_config(self, name):
            return json.loads(json.dumps(resolved))

        def list_apps(self, host):
            return ["site"]

        def load_app_config(self, host, app):
            return {"database": {"password": SECRET, "root_password": SECRET}}

    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: CM())

    data = config_backup._collect_configs()

    dumped = json.dumps(data)
    assert SECRET not in dumped, "a resolved credential reached the JSON export"
    host = data["hosts"]["prod"]
    assert host["ssh_password"] == "[REDACTED]"
    assert host["database"]["root_password"] == "[REDACTED]"
    # The reference names a vault item — not a value — and is what makes a restore work.
    assert host["ssh_password_ref"] == "vault:host/prod/ssh_password"
    assert host["database"]["user"] == "app"


# ── 3. database commands ────────────────────────────────────────────────────


def test_with_secret_env_moves_the_value_to_stdin():
    cmd, stdin = with_secret_env("mysql -u root -e 'SELECT 1'", {"MYSQL_PWD": SECRET})

    assert SECRET not in cmd
    assert stdin == f"{SECRET}\n"
    assert cmd.startswith("sh -c ")
    assert "IFS= read -r MYSQL_PWD" in cmd and "export MYSQL_PWD" in cmd


def test_with_secret_env_without_a_value_changes_nothing():
    assert with_secret_env("psql -l", {"PGPASSWORD": None}) == ("psql -l", None)
    assert with_secret_env("psql -l", {"PGPASSWORD": ""}) == ("psql -l", None)


def test_with_secret_env_refuses_a_multiline_value_without_echoing_it():
    with pytest.raises(ValueError) as exc:
        with_secret_env("psql -l", {"PGPASSWORD": "line1\nline2"})
    assert "line1" not in str(exc.value)


@pytest.mark.parametrize("db_type", ["mysql", "mariadb", "postgresql"])
@pytest.mark.parametrize("container", [None, "db1"])
def test_db_query_command_never_carries_the_password(db_type, container):
    cmd, stdin = db_mod._build_db_command(db_type, "SELECT 1", "app", SECRET, "site", container)

    assert SECRET not in cmd
    assert "-p'" not in cmd and "PGPASSWORD=" not in cmd
    assert stdin == f"{SECRET}\n"
    var = "PGPASSWORD" if db_type == "postgresql" else "MYSQL_PWD"
    if container:
        # forwarded into the container by NAME only
        assert f"docker exec -e {var} db1" in cmd


def test_db_query_runs_with_the_password_on_stdin():
    calls = []

    class Disco:
        def _execute_ssh(self, command, stdin=None):
            calls.append((command, stdin))
            return True, "1", ""

    db_mod._execute_db_query(Disco(), "SELECT 1", "mysql", "root", SECRET, "site")

    (command, stdin), = calls
    assert SECRET not in command and stdin == f"{SECRET}\n"


def test_passwordless_db_command_is_unchanged():
    cmd, stdin = db_mod._build_db_command("mysql", "SELECT 1", "root", None, None, None)
    assert cmd == "mysql -uroot -e 'SELECT 1'" and stdin is None


def test_deck_db_builders_take_no_password_and_wrap_at_execution():
    from navig.gateway.deck.routes import database as deck_db

    plain = deck_db._build_query_cmd("mysql", "site", "root", "SELECT 1")
    assert plain.startswith("mysql -u root")  # what the policy check and the audit see
    cmd, stdin = deck_db._secret_stdin(plain, "mysql", SECRET)
    assert SECRET not in cmd and stdin == f"{SECRET}\n"
    cmd, stdin = deck_db._secret_stdin(deck_db._build_list_cmd("postgres", "app"), "postgres", SECRET)
    assert "read -r PGPASSWORD" in cmd and SECRET not in cmd


def test_agent_db_dump_tool_keeps_the_password_off_the_command_line(monkeypatch):
    import asyncio

    from navig.agent.tools import devops_tools

    calls = []

    class Disco:
        def _execute_ssh(self, command, stdin=None):
            calls.append((command, stdin))
            return True, "", ""

    monkeypatch.setattr(devops_tools, "_resolve_host", lambda *a: ("prod", {}))
    monkeypatch.setattr(devops_tools, "_get_discovery", lambda cfg: Disco())

    result = asyncio.run(
        devops_tools.NavigDbDumpTool().run(
            {"database": "site", "output": "/tmp/site.sql.gz", "db_password": SECRET}
        )
    )

    assert result.success, result.error
    (command, stdin), = calls
    assert SECRET not in command and stdin == f"{SECRET}\n"
    assert "set -o pipefail" in command  # the gzip pipefail guard survived the wrap


# ── 4. log redaction ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "line",
    [
        "mysql -uroot -pS3cretValue site -e 'SELECT 1'",
        "mysqldump -u'root' -p'S3cretValue' site",
        "mariadb -u root -p'S3cr'\"'\"'etValue' -e x",
    ],
)
def test_redactor_catches_an_attached_mysql_password(line):
    out = redact_sensitive_text(line)
    assert "S3cr" not in out, out
    assert "etValue" not in out, out


def test_redactor_leaves_unrelated_p_flags_alone():
    assert redact_sensitive_text("gcc -pthread main.c") == "gcc -pthread main.c"
    assert redact_sensitive_text("find . -print") == "find . -print"
