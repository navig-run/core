"""Gate 0F — host secrets leave plaintext YAML and live in the vault.

Covers the migration command (`navig security migrate-host-secrets`), the
write/create paths (nothing new persists a password in the clear), the read-side
shim (legacy plaintext keeps resolving unchanged), and the fail-safe (a broken
vault refuses the write instead of falling back to plaintext).

Isolation discipline: every test plants NAVIG_CONFIG_DIR / NAVIG_DATA_DIR at a
per-test tmp FIRST and asserts the resolved ``hosts_dir`` BEFORE writing —
`ConfigManager(config_dir=…)` has been known to still save into the real config,
so the resolved path is proven, not assumed (memory: navig-adhoc-probe-wrote-the-
real-key-store). NAVIG_HOME is never used.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from navig.commands import security
from navig.core.host_secrets import (
    HOST_SECRET_KEYS,
    HostSecretError,
    resolve_host_secret,
    scan_plaintext_host_secrets,
)

SUPERSECRET = "SUPERSECRET"
ROOTSECRET = "ROOTSECRET"


@pytest.fixture
def host_env(tmp_path, monkeypatch):
    """Isolated config/vault/data roots + the live ConfigManager singleton.

    Returns the hosts_dir actually resolved — asserted to live under the tmp
    config dir BEFORE any test file is planted.
    """
    from navig.config import get_config_manager, reset_config_manager

    cfg = tmp_path / "cfg"
    data = tmp_path / "data"
    cfg.mkdir()
    data.mkdir()
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    reset_config_manager()
    cm = get_config_manager()

    # The isolation proof — fail HERE, before the first write, if the manager
    # resolved anywhere near the operator's real config.
    assert Path(cm.global_config_dir) == cfg, (
        f"global_config_dir escaped isolation: {cm.global_config_dir}"
    )
    hosts = Path(cm.hosts_dir)
    assert hosts.is_relative_to(cfg), f"hosts_dir escaped isolation: {hosts}"
    hosts.mkdir(parents=True, exist_ok=True)
    return cm


def _plant(hosts_dir: Path, name: str = "prodbox", *, with_secrets: bool = True) -> Path:
    """Write a host YAML the way the pre-0F code used to leave them."""
    data = {
        "name": name,
        "host": "203.0.113.10",
        "port": 22,
        "user": "root",
        "ssh_key": "~/.ssh/id_rsa",
        "database": {
            "type": "mysql",
            "root_user": "root",
        },
    }
    if with_secrets:
        data["ssh_password"] = SUPERSECRET
        data["database"]["root_password"] = ROOTSECRET
    path = hosts_dir / f"{name}.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Dry run — THE critical assertion: the value never reaches the output
# ---------------------------------------------------------------------------


def test_dry_run_prints_host_and_key_but_never_the_value(host_env, capsys):
    path = _plant(host_env.hosts_dir)

    code = security.migrate_host_secrets({}, apply=False)

    out = capsys.readouterr().out
    assert code == 0
    # The critical assertion — the planted value must not exist anywhere in output.
    assert SUPERSECRET not in out, "dry-run leaked a plaintext secret"
    assert ROOTSECRET not in out, "dry-run leaked a nested plaintext secret"
    # While the host, the keys and the target refs ARE present.
    assert "prodbox" in out
    assert "ssh_password" in out
    assert "root_password" in out
    assert "vault:host/prodbox/ssh_password" in out
    assert str(len(SUPERSECRET)) in out
    assert "DRY RUN" in out
    # Nothing was written: no backup, file still plaintext, no vault ref added.
    assert not list(host_env.hosts_dir.glob("*.yaml.bak.*"))
    text = path.read_text(encoding="utf-8")
    assert SUPERSECRET in text
    assert "ssh_password_ref" not in text


def test_dry_run_with_nothing_to_do_exits_zero_and_says_so(host_env, capsys):
    _plant(host_env.hosts_dir, "cleanbox", with_secrets=False)

    code = security.migrate_host_secrets({}, apply=False)

    assert code == 0
    assert "No plaintext host secrets" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# --apply — vault + backup + ref-only rewrite, readable back through the loader
# ---------------------------------------------------------------------------


def test_apply_vaults_backs_up_and_resolves_back(host_env, capsys):
    path = _plant(host_env.hosts_dir)

    code = security.migrate_host_secrets({}, apply=True)

    assert code == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert SUPERSECRET not in out, "apply summary leaked the value"

    text = path.read_text(encoding="utf-8")
    assert "ssh_password_ref" in text
    assert "root_password_ref" in text
    assert SUPERSECRET not in text
    assert ROOTSECRET not in text
    assert re.search(r"(?m)^ssh_password:", text) is None
    assert re.search(r"(?m)^\s+root_password:", text) is None

    backups = list(host_env.hosts_dir.glob("prodbox.yaml.bak.*"))
    assert len(backups) == 1, "apply must leave exactly one .yaml.bak.<stamp> backup"
    assert SUPERSECRET in backups[0].read_text(encoding="utf-8")

    # Round trip through the real loader (the compat shim) …
    loaded = host_env.load_host_config("prodbox")
    assert loaded["ssh_password"] == SUPERSECRET
    assert loaded["database"]["root_password"] == ROOTSECRET

    # … and straight off the raw file through resolve_host_secret.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "ssh_password" not in raw
    assert resolve_host_secret(raw, "ssh_password") == SUPERSECRET
    assert resolve_host_secret(raw, "database.root_password") == ROOTSECRET


def test_rollback_restores_the_newest_backup(host_env, capsys):
    path = _plant(host_env.hosts_dir)
    assert security.migrate_host_secrets({}, apply=True) == 0
    capsys.readouterr()

    code = security.migrate_host_secrets({}, rollback=True)

    assert code == 0
    text = path.read_text(encoding="utf-8")
    assert SUPERSECRET in text, "rollback must restore the pre-migration plaintext"
    assert "ssh_password_ref" not in text


def test_rollback_without_backups_refuses(host_env, capsys):
    _plant(host_env.hosts_dir)

    code = security.migrate_host_secrets({}, rollback=True)

    assert code == 1
    assert "No host-secret backups" in capsys.readouterr().out


def test_apply_and_rollback_are_mutually_exclusive(host_env):
    assert security.migrate_host_secrets({}, apply=True, rollback=True) == 2


# ---------------------------------------------------------------------------
# Legacy compatibility — plaintext keeps resolving, unchanged
# ---------------------------------------------------------------------------


def test_legacy_plaintext_resolves_unchanged_without_touching_the_vault(
    host_env, monkeypatch
):
    path = _plant(host_env.hosts_dir)

    # If anything on this path tried the vault it would explode: point it at a
    # directory whose parent is a FILE, so Vault() construction cannot mkdir.
    blocker = host_env.base_dir / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(blocker / "vault"))

    # resolve_host_secret: plaintext wins, no ref needed.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert resolve_host_secret(raw, "ssh_password") == SUPERSECRET
    assert resolve_host_secret(raw, "database.root_password") == ROOTSECRET

    # The loader shim delivers the same dict shape it always did.
    loaded = host_env.load_host_config("prodbox")
    assert loaded["ssh_password"] == SUPERSECRET
    assert loaded["database"]["root_password"] == ROOTSECRET

    # Nothing configured → None, still no vault touch.
    assert resolve_host_secret({"name": "empty"}, "ssh_password") is None


# ---------------------------------------------------------------------------
# Fail-safe — vault unavailable ⇒ clear error, NO plaintext written
# ---------------------------------------------------------------------------


def test_save_refuses_to_write_plaintext_when_the_vault_is_broken(
    host_env, monkeypatch, capsys
):
    blocker = host_env.base_dir / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(blocker / "vault"))

    with pytest.raises(HostSecretError) as excinfo:
        host_env.save_host_config(
            "brokenvault",
            {"name": "brokenvault", "host": "198.51.100.7", "user": "root",
             "ssh_password": SUPERSECRET},
        )

    # Clear, typed error — and never the value.
    assert SUPERSECRET not in str(excinfo.value)
    assert "vault" in str(excinfo.value)
    assert not (host_env.hosts_dir / "brokenvault.yaml").exists(), (
        "a failed vault write must leave NO file behind — not a plaintext one"
    )


def test_create_server_config_stores_only_the_ref(host_env):
    cfg = host_env.create_server_config(
        name="newbox",
        host="198.51.100.9",
        port=22,
        user="root",
        ssh_key="~/.ssh/id_rsa",
        ssh_password=SUPERSECRET,
    )

    # Returned dict and persisted YAML both carry the ref, never the password.
    assert "ssh_password" not in cfg
    assert cfg["ssh_password_ref"] == "vault:host/newbox/ssh_password"

    text = (host_env.hosts_dir / "newbox.yaml").read_text(encoding="utf-8")
    assert SUPERSECRET not in text
    assert "ssh_password_ref" in text
    assert re.search(r"(?m)^ssh_password:", text) is None

    assert host_env.load_host_config("newbox")["ssh_password"] == SUPERSECRET


def test_migrate_apply_fails_safe_when_the_vault_is_broken(host_env, monkeypatch, capsys):
    path = _plant(host_env.hosts_dir)
    before = path.read_text(encoding="utf-8")

    blocker = host_env.base_dir / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv("NAVIG_VAULT_DIR", str(blocker / "vault"))

    code = security.migrate_host_secrets({}, apply=True)

    assert code == 1
    out = capsys.readouterr().out
    assert SUPERSECRET not in out, "the failure report leaked the value"
    assert "vault" in out.lower()
    # File untouched: still plaintext, no backup, no half-written ref.
    assert path.read_text(encoding="utf-8") == before
    assert not list(host_env.hosts_dir.glob("*.yaml.bak.*"))


# ---------------------------------------------------------------------------
# Wiring — the command surface and the scan contract
# ---------------------------------------------------------------------------


def test_security_group_is_registered_on_the_cli():
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    assert _EXTERNAL_CMD_MAP["security"] == ("navig.commands.security", "security_app")


def test_migrate_host_secrets_command_invokes_through_typer(host_env, capsys):
    from typer.testing import CliRunner

    _plant(host_env.hosts_dir)

    result = CliRunner().invoke(
        security.security_app, ["migrate-host-secrets"], catch_exceptions=False
    )

    assert result.exit_code == 0
    assert SUPERSECRET not in result.output
    assert "ssh_password" in result.output


def test_scan_rows_carry_length_not_value(host_env):
    _plant(host_env.hosts_dir)

    rows = scan_plaintext_host_secrets(host_env.hosts_dir)

    keys = {row["key"] for row in rows}
    assert keys == {"ssh_password", "database.root_password"}
    for row in rows:
        assert SUPERSECRET not in str(row)
        assert ROOTSECRET not in str(row)
        assert isinstance(row["length"], int) and row["length"] > 0
        assert row["ref"].startswith("vault:host/prodbox/")


def test_host_secret_keys_match_the_fields_the_write_paths_persist():
    """The tuple must cover what create_server_config / host.py actually store."""
    import inspect

    from navig.config import ConfigManager

    src = inspect.getsource(ConfigManager.create_server_config)
    assert "ssh_password" in src
    # ssh_password + both database fields — the real password-ish set found in
    # create_server_config and commands/host.py's database block.
    assert HOST_SECRET_KEYS == (
        "ssh_password",
        "database.root_password",
        "database.password",
    )


# ---------------------------------------------------------------------------
# Multi-scope scan — the loader merges app-local + global; so must the migration
# ---------------------------------------------------------------------------


def _two_scopes(host_env, tmp_path, monkeypatch):
    """Reproduce the real bug surface: active scope = app dir, secrets in GLOBAL.

    HostManager.list_hosts merges get_config_directories(); the command used to
    scan only cm.hosts_dir, so a run from inside a space said "nothing to
    migrate" while the global dir still held plaintext.
    """
    app_root = tmp_path / "appspace" / ".navig"
    (app_root / "hosts").mkdir(parents=True, exist_ok=True)
    cm = host_env
    monkeypatch.setattr(cm, "hosts_dir", app_root / "hosts", raising=False)
    monkeypatch.setattr(
        cm, "get_config_directories", lambda: [app_root, tmp_path / "cfg"]
    )
    return cm, app_root / "hosts", Path(cm.hosts_dir).parent / "hosts"  # global hosts


def test_dry_run_sees_global_secrets_when_active_scope_is_an_app_dir(
    host_env, tmp_path, monkeypatch, capsys
):
    cm, app_hosts, _ = _two_scopes(host_env, tmp_path, monkeypatch)
    global_hosts = tmp_path / "cfg" / "hosts"
    _plant(global_hosts, "prodbox")

    # Spy on the table printer but CALL THROUGH — the rendered output stays the
    # subject of the value-safety assertion below, so it cannot go vacuous.
    real_printer = security._print_secret_rows
    captured: dict = {}

    def _spy(title, rows):
        captured["rows"] = rows
        real_printer(title, rows)

    monkeypatch.setattr(security, "_print_secret_rows", _spy)

    code = security.migrate_host_secrets({}, apply=False)

    assert code == 0
    out = capsys.readouterr().out
    assert SUPERSECRET not in out
    assert "prodbox" in out and "ssh_password" in out, (
        "a secret in the GLOBAL scope must appear in the dry run even when the "
        "active config scope is an app dir — the old single-dir scan missed it"
    )
    # The scan must come from the file that actually carries it (Rich truncates
    # long paths in the rendered table, so assert on the captured row, not out).
    # _plant writes two secrets — two rows, one file.
    files = {Path(r["file"]) for r in captured["rows"]}
    assert files == {global_hosts / "prodbox.yaml"}
    assert len(captured["rows"]) == 2, "both planted secrets are reported"


def test_apply_and_rollback_reach_every_scope(host_env, tmp_path, monkeypatch, capsys):
    cm, app_hosts, _ = _two_scopes(host_env, tmp_path, monkeypatch)
    global_hosts = tmp_path / "cfg" / "hosts"
    f_app = _plant(app_hosts, "appbox")
    f_global = _plant(global_hosts, "prodbox")

    assert security.migrate_host_secrets({}, apply=True) == 0
    for f in (f_app, f_global):
        text = Path(f).read_text(encoding="utf-8")
        assert "_ref" in text and SUPERSECRET not in text, f"{f.name} not migrated"
    capsys.readouterr()

    assert security.migrate_host_secrets({}, rollback=True) == 0
    for f, name in ((f_app, "appbox"), (f_global, "prodbox")):
        text = Path(f).read_text(encoding="utf-8")
        assert SUPERSECRET in text, f"{name} not restored in {f}"


# ---------------------------------------------------------------------------
# --purge-backups — the .bak is the last plaintext copy; delete only when the
# vault provably serves the host (2026-10-15, vault-export-leaks)
# ---------------------------------------------------------------------------


def test_purge_backups_deletes_the_plaintext_copy_once_the_vault_serves_it(host_env, capsys):
    _plant(host_env.hosts_dir)
    assert security.migrate_host_secrets({}, apply=True) == 0
    assert list(host_env.hosts_dir.glob("prodbox.yaml.bak.*"))
    capsys.readouterr()

    code = security.migrate_host_secrets({}, purge_backups=True)

    out = capsys.readouterr().out
    assert code == 0, out
    assert not list(host_env.hosts_dir.glob("*.bak.*")), "the plaintext backup survived"
    assert SUPERSECRET not in out and ROOTSECRET not in out
    # The host still resolves — only the backup went.
    assert host_env.load_host_config("prodbox")["ssh_password"] == SUPERSECRET


def test_purge_backups_keeps_a_backup_the_vault_cannot_replace(host_env, monkeypatch, capsys):
    _plant(host_env.hosts_dir)
    assert security.migrate_host_secrets({}, apply=True) == 0
    capsys.readouterr()

    # The vault item disappears: the backup is now the only copy of the secret.
    from navig.vault import core as vault_core

    vault_core.get_vault().delete("host/prodbox/ssh_password")

    code = security.migrate_host_secrets({}, purge_backups=True)

    out = capsys.readouterr().out
    assert code == 1, out
    assert len(list(host_env.hosts_dir.glob("prodbox.yaml.bak.*"))) == 1
    assert "kept" in out
    assert SUPERSECRET not in out


def test_purge_backups_is_exclusive_with_apply_and_rollback(host_env):
    assert security.migrate_host_secrets({}, apply=True, purge_backups=True) == 2
    assert security.migrate_host_secrets({}, rollback=True, purge_backups=True) == 2