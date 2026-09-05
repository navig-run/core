from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import navig.commands.backup as backup_mod

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

        # Mirrors RemoteOperations.execute_command(command, server_config, …).
        # This fake used to take the command ALONE, which is exactly how the
        # missing server_config argument stayed invisible to a test whose own
        # name promises there is no TypeError.
        def execute_command(self, _cmd, _server_config=None, **_kw):
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


def _wire_server_mocks(monkeypatch, tmp_path, *, execute_stdout: str):
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

        # Mirrors RemoteOperations.execute_command(command, server_config, …).
        # This fake used to take the command ALONE, which is exactly how the
        # missing server_config argument stayed invisible to a test whose own
        # name promises there is no TypeError.
        def execute_command(self, _cmd, _server_config=None, **_kw):
            return subprocess.CompletedProcess(
                args=["x"], returncode=0, stdout=execute_stdout, stderr=""
            )

    for modname, ns in (
        ("navig.config", SimpleNamespace(get_config_manager=lambda: cfg_mgr)),
        ("navig.remote", SimpleNamespace(RemoteOperations=_Remote)),
        ("navig.cli.recovery", SimpleNamespace(require_active_server=lambda *_a, **_kw: "prod")),
    ):
        monkeypatch.setitem(__import__("sys").modules, modname, ns)


def test_backup_system_config_all_failed_raises_exit(monkeypatch, tmp_path):
    """Every file EXISTS but its download fails (0 saved) → the command must exit
    non-zero, not print '✅ complete'. Otherwise a nightly `navig backup` checking
    $LASTEXITCODE (and backup_all, which collects the raised typer.Exit) records a
    zero-data run as success."""
    import typer

    _wire_server_mocks(monkeypatch, tmp_path, execute_stdout="exists\n")

    def _scp_fail(*_a, **_kw):
        raise subprocess.CalledProcessError(1, "scp")

    monkeypatch.setattr(backup_mod, "_run_scp_command", _scp_fail)

    with pytest.raises(typer.Exit):
        backup_mod.backup_system_config(name="unit", options={})


def test_backup_system_config_all_skipped_does_not_raise(monkeypatch, tmp_path):
    """All files missing = nothing to back up (skipped), which is NOT a failure —
    the exit-honesty guard must not over-raise on a skip-only run."""
    _wire_server_mocks(monkeypatch, tmp_path, execute_stdout="missing\n")
    monkeypatch.setattr(backup_mod, "_run_scp_command", lambda *_a, **_kw: None)

    # Must return normally (no typer.Exit) — skip is not failure.
    backup_mod.backup_system_config(name="unit", options={})


def _wire_server_mocks_by_command(monkeypatch, tmp_path, responder):
    """Like ``_wire_server_mocks`` but the fake answers per COMMAND.

    Needed for the hestia skip case: HestiaCP must look *installed* while every
    directory looks *missing*, which one fixed stdout cannot express.
    """
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

        def execute_command(self, cmd, _server_config=None, **_kw):
            return subprocess.CompletedProcess(
                args=["x"], returncode=0, stdout=responder(cmd), stderr=""
            )

    for modname, ns in (
        ("navig.config", SimpleNamespace(get_config_manager=lambda: cfg_mgr)),
        ("navig.remote", SimpleNamespace(RemoteOperations=_Remote)),
        ("navig.cli.recovery", SimpleNamespace(require_active_server=lambda *_a, **_kw: "prod")),
    ):
        monkeypatch.setitem(__import__("sys").modules, modname, ns)


# ── Exit honesty for the OTHER two backup steps ───────────────────────
#
# backup_system_config and backup_all_databases learned to exit non-zero on a
# zero-data run. backup_hestia and backup_web_config did not, and backup_all
# already collects each substep's typer.Exit — so `navig backup run --all` would
# record a HestiaCP/webserver backup that saved nothing as a success.


def test_backup_hestia_all_failed_raises_exit(monkeypatch, tmp_path):
    """Every directory exists but every download fails → exit non-zero."""
    import typer

    _wire_server_mocks(monkeypatch, tmp_path, execute_stdout="exists\n")

    def _scp_fail(*_a, **_kw):
        raise subprocess.CalledProcessError(1, "scp")

    monkeypatch.setattr(backup_mod, "_run_scp_command", _scp_fail)

    with pytest.raises(typer.Exit):
        backup_mod.backup_hestia(name="unit", options={})


def test_backup_hestia_all_skipped_does_not_raise(monkeypatch, tmp_path):
    """HestiaCP installed but every directory absent = skipped, not failed.

    A partial install must not turn a comprehensive backup red.
    """
    def _responder(cmd: str) -> str:
        # The install probe must say "present"; every `test -d` says missing.
        return "installed\n" if "v-list-users" in cmd else "missing\n"

    _wire_server_mocks_by_command(monkeypatch, tmp_path, _responder)
    monkeypatch.setattr(backup_mod, "_run_scp_command", lambda *_a, **_kw: None)

    backup_mod.backup_hestia(name="unit", options={})  # must not raise


def test_backup_web_config_all_failed_raises_exit(monkeypatch, tmp_path):
    """Config files exist on the server but every scp fails → exit non-zero.

    Before this, every failure was swallowed by `except ...: pass`, so the run
    printed "✅ Web server backup complete · Nginx files: 0" and exited 0.
    """
    import typer

    _wire_server_mocks(monkeypatch, tmp_path, execute_stdout="exists\n")

    def _scp_fail(*_a, **_kw):
        raise subprocess.CalledProcessError(1, "scp")

    monkeypatch.setattr(backup_mod, "_run_scp_command", _scp_fail)

    with pytest.raises(typer.Exit):
        backup_mod.backup_web_config(name="unit", options={})


def test_backup_web_config_no_webserver_installed_does_not_raise(monkeypatch, tmp_path):
    """Neither nginx nor apache present = nothing to back up, not a failure."""
    _wire_server_mocks(monkeypatch, tmp_path, execute_stdout="missing\n")
    monkeypatch.setattr(backup_mod, "_run_scp_command", lambda *_a, **_kw: None)

    backup_mod.backup_web_config(name="unit", options={})  # must not raise


def test_backup_web_config_records_failures_in_metadata(monkeypatch, tmp_path):
    """A failed download must appear in the metadata, and must NOT be counted
    as a backed-up file.

    `nginx_files` used to be `len(results["nginx"])`, which was only ever right
    because failures were dropped on the floor. Recording them without fixing
    the count would make the same number lie the other way.
    """
    import typer

    _wire_server_mocks(monkeypatch, tmp_path, execute_stdout="exists\n")
    monkeypatch.setattr(
        backup_mod, "_run_scp_command",
        lambda *_a, **_kw: (_ for _ in ()).throw(subprocess.CalledProcessError(1, "scp")),
    )

    with pytest.raises(typer.Exit):
        backup_mod.backup_web_config(name="unit", options={})

    meta_file = next((tmp_path / "backups").rglob("metadata.json"))
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["nginx_files"] == 0
    assert meta["apache_files"] == 0
    failed = [r for v in meta["details"].values() for r in v if r.get("status") == "failed"]
    assert failed, "failures were not recorded in the metadata at all"


# ── `navig backup restore` must not claim a restore it did not do ─────
#
# It used to print "⚠️ This will overwrite existing files/databases", take the
# operator's `y`, then print "requires manual review" and exit 0 - three lies in
# one command, on the path a person reaches while their server is already down.


def _make_backup(tmp_path, name="prod_full", components=("configs", "databases")):
    root = tmp_path / "backups" / name
    for comp in components:
        f = root / comp / "file.txt"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x" * 1024, encoding="utf-8")
    (root / "metadata.json").write_text('{"type": "full"}', encoding="utf-8")
    return root


def _wire_backups_dir(monkeypatch, tmp_path):
    monkeypatch.setitem(
        __import__("sys").modules,
        "navig.config",
        SimpleNamespace(get_config_manager=lambda: SimpleNamespace(
            backups_dir=tmp_path / "backups")),
    )


def test_restore_exits_non_zero_and_claims_nothing(monkeypatch, tmp_path, capsys):
    """Exit 1, and no wording that suggests a restore happened."""
    import typer

    _make_backup(tmp_path)
    _wire_backups_dir(monkeypatch, tmp_path)

    with pytest.raises(typer.Exit) as exc:
        backup_mod.restore_backup_cmd("prod_full", None, {})
    assert exc.value.exit_code == 1

    out = capsys.readouterr().out
    assert "not implemented" in out.lower()
    # The old flow's exact words - a destructive claim about something that never runs.
    assert "will overwrite" not in out.lower()
    assert "🔄 Restoring from backup" not in out


def test_restore_never_prompts_for_consent(monkeypatch, tmp_path):
    """No confirmation prompt: consent to an action that cannot happen is meaningless.

    It also used to HANG a non-interactive caller on `input()` when --force was
    absent.
    """
    _make_backup(tmp_path)
    _wire_backups_dir(monkeypatch, tmp_path)

    def _boom(*_a, **_kw):  # pragma: no cover - must never be reached
        raise AssertionError("restore asked for confirmation it cannot honour")

    monkeypatch.setattr("builtins.input", _boom)

    import typer

    with pytest.raises(typer.Exit):
        backup_mod.restore_backup_cmd("prod_full", None, {})


def test_restore_lists_the_components_it_found(monkeypatch, tmp_path, capsys):
    """The useful half: say what is in the backup so a manual restore is possible."""
    import typer

    _make_backup(tmp_path, components=("configs", "databases", "hestia"))
    _wire_backups_dir(monkeypatch, tmp_path)

    with pytest.raises(typer.Exit):
        backup_mod.restore_backup_cmd("prod_full", None, {})

    out = capsys.readouterr().out
    for comp in ("configs", "databases", "hestia"):
        assert comp in out


def test_restore_json_reports_restored_false(monkeypatch, tmp_path, capsys):
    """A machine caller must be able to see that nothing was restored."""
    import typer

    _make_backup(tmp_path)
    _wire_backups_dir(monkeypatch, tmp_path)

    with pytest.raises(typer.Exit) as exc:
        backup_mod.restore_backup_cmd("prod_full", None, {"json": True})
    assert exc.value.exit_code == 1

    data = json.loads(capsys.readouterr().out)
    assert data["restored"] is False
    assert data["components"], "the inventory must still be reported"


def test_restore_missing_backup_still_exits_2(monkeypatch, tmp_path):
    """Usage error stays distinguishable from the not-implemented failure."""
    import typer

    (tmp_path / "backups").mkdir()
    _wire_backups_dir(monkeypatch, tmp_path)

    with pytest.raises(typer.Exit) as exc:
        backup_mod.restore_backup_cmd("nope", None, {})
    assert exc.value.exit_code == 2


def test_restore_dry_run_does_not_promise_a_restore(monkeypatch, tmp_path, capsys):
    """'[DRY RUN] Would restore from: …' was itself untrue - it would not."""
    _make_backup(tmp_path)
    _wire_backups_dir(monkeypatch, tmp_path)

    backup_mod.restore_backup_cmd("prod_full", None, {"dry_run": True})

    out = capsys.readouterr().out
    assert "not implemented" in out.lower()
    assert "would restore from" not in out.lower()


# ── ctx.obj is guarded once, at the group callback ─────────────────────


def test_backup_subcommands_survive_a_missing_ctx_obj(tmp_path, monkeypatch):
    """Every subcommand here reads `ctx.obj["json"]` / `.get("yes")`.

    The root `navig` callback populates it, but reaching a subcommand any other
    way left it None, and `ctx.obj.get(...)` on None raises AttributeError before
    the command body runs. The group callback now guards it once for all 16 sites
    — this pins that the callback really does run first and that children inherit
    `obj`, rather than trusting that it does.
    """
    from typer.testing import CliRunner

    from navig.commands.backup import backup_app

    _wire_backups_dir(monkeypatch, tmp_path)
    (tmp_path / "backups").mkdir(exist_ok=True)

    # `restore` is the shortest path that touches ctx.obj in BOTH ways:
    # `ctx.obj["force"] = force` then `.get(...)` inside restore_backup_cmd.
    res = CliRunner().invoke(backup_app, ["restore", "nope"], obj=None)

    assert not isinstance(res.exception, AttributeError), res.exception
    # Exit 2 = "backup not found", i.e. the command body ran and made a decision.
    assert res.exit_code == 2, res.output


def test_backup_list_survives_a_missing_ctx_obj(tmp_path, monkeypatch):
    """A second subcommand, so the guard is shown to be group-wide, not per-command.

    Asserts on the SPECIFIC failure the guard prevents rather than "no
    AttributeError": this fake config manager does not implement everything
    `list` wants, and a blanket assertion would fail for that unrelated reason
    while claiming to be about ctx.obj.
    """
    from typer.testing import CliRunner

    from navig.commands.backup import backup_app

    _wire_backups_dir(monkeypatch, tmp_path)
    (tmp_path / "backups").mkdir(exist_ok=True)

    res = CliRunner().invoke(backup_app, ["list"], obj=None)
    assert "NoneType" not in str(res.exception), res.exception
