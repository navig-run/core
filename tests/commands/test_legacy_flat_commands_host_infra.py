"""Tests for navig/cli/legacy_flat_commands.py and navig/cli/host_infra.py.

Both modules register deprecated/legacy CLI command groups on a root Typer app.
We verify:
  - Registration completes without error.
  - Each deprecated command calls deprecation_warning and its delegate.
  - Host-infra tunnel/monitor/security sub-apps route to the right delegates.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

import navig.cli.legacy_flat_commands as _leg
import navig.cli.host_infra as _hi

runner = CliRunner()

# Base ctx.obj dict passed via runner.invoke(app, args, obj=...) so that
# commands can do ctx.obj["key"] = value without AttributeError.
_OBJ = {"host": "test-host", "yes": True}


def _leg_app() -> typer.Typer:
    app = typer.Typer()
    _leg.register_legacy_flat_commands(app)
    return app


def _hi_app() -> typer.Typer:
    app = typer.Typer()
    _hi.register_host_infra_commands(app)
    return app


# ─── legacy_flat_commands: registration ─────────────────────────────────────

class TestLegacyRegistration:
    def test_register_completes(self):
        app = typer.Typer()
        _leg.register_legacy_flat_commands(app)  # should not raise

    def test_all_commands_registered(self):
        app = _leg_app()
        names = {c.name for c in app.registered_commands}
        expected_subset = {
            "upload", "download", "list", "delete", "mkdir", "chmod", "chown",
            "cat", "write-file", "ls", "tree",
            "db-list", "db-tables", "db-query", "db-dump",
            "logs", "health", "restart", "sql", "sqlfile",
            "restore", "backup-config", "backup-db-all", "backup-all",
            "list-backups", "restore-backup",
        }
        assert expected_subset.issubset(names)


# ─── legacy_flat_commands: file-ops ──────────────────────────────────────────

class TestLegacyFileOps:
    def test_upload_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files.upload_file_cmd") as fn:
            runner.invoke(app, ["upload", "local.txt"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_download_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files.download_file_cmd") as fn:
            runner.invoke(app, ["download", "/remote/file.txt"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_list_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files.list_remote_directory") as fn:
            runner.invoke(app, ["list", "/remote/dir"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_delete_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.delete_file_cmd") as fn:
            runner.invoke(app, ["delete", "/remote/file.txt"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_mkdir_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.mkdir_cmd") as fn:
            runner.invoke(app, ["mkdir", "/remote/dir"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_chmod_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.chmod_cmd") as fn:
            runner.invoke(app, ["chmod", "/remote/file", "755"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_chown_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.chown_cmd") as fn:
            runner.invoke(app, ["chown", "/remote/file", "www-data"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_cat_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.cat_file_cmd") as fn:
            runner.invoke(app, ["cat", "/remote/file.txt"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_ls_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.list_dir_cmd") as fn:
            runner.invoke(app, ["ls", "/remote/dir"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_tree_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.files_advanced.tree_cmd") as fn:
            runner.invoke(app, ["tree", "/remote/dir"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()


# ─── legacy_flat_commands: db-ops ────────────────────────────────────────────

class TestLegacyDbOps:
    def test_db_list_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.database_advanced.list_databases_cmd") as fn:
            runner.invoke(app, ["db-list"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_db_tables_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.database_advanced.list_tables_cmd") as fn:
            runner.invoke(app, ["db-tables", "mydb"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_db_optimize_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.database_advanced.optimize_table_cmd") as fn:
            runner.invoke(app, ["db-optimize", "users"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_db_repair_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.database_advanced.repair_table_cmd") as fn:
            runner.invoke(app, ["db-repair", "users"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_db_query_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.db.db_query_cmd") as fn:
            runner.invoke(app, ["db-query", "SELECT 1"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_db_dump_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.db.db_dump_cmd") as fn:
            runner.invoke(app, ["db-dump", "mydb"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_sql_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.db.db_query_cmd") as fn:
            runner.invoke(app, ["sql", "SELECT 1"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_sqlfile_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.database.execute_sql_file") as fn:
            runner.invoke(app, ["sqlfile", "query.sql"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()


# ─── legacy_flat_commands: monitoring / backup ───────────────────────────────

class TestLegacyMonitorBackup:
    def test_logs_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.monitoring.view_service_logs") as fn:
            runner.invoke(app, ["logs", "nginx"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_health_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.monitoring.run_health_check") as fn:
            runner.invoke(app, ["health"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_restart_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.monitoring.restart_remote_service") as fn:
            runner.invoke(app, ["restart", "nginx"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_restore_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.database.restore_database") as fn:
            runner.invoke(app, ["restore", "backup.sql"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_backup_config_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.backup.backup_system_config") as fn:
            runner.invoke(app, ["backup-config"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_backup_db_all_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.backup.backup_all_databases") as fn:
            runner.invoke(app, ["backup-db-all"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_backup_all_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.backup.backup_all") as fn:
            runner.invoke(app, ["backup-all"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_list_backups_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.backup.list_backups_cmd") as fn:
            runner.invoke(app, ["list-backups"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_restore_backup_delegates(self):
        app = _leg_app()
        with patch("navig.cli.legacy_flat_commands.deprecation_warning") as dw, \
             patch("navig.commands.backup.restore_backup_cmd") as fn:
            runner.invoke(app, ["restore-backup", "backup-2024"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()


# ─── host_infra: registration ────────────────────────────────────────────────

class TestHostInfraRegistration:
    def test_register_completes(self):
        app = typer.Typer()
        _hi.register_host_infra_commands(app)  # should not raise

    def test_tunnel_sub_app_present(self):
        app = _hi_app()
        grp_names = {g.name for g in app.registered_groups}
        assert "tunnel" in grp_names


# ─── host_infra: tunnel commands ─────────────────────────────────────────────

class TestHostInfraTunnel:
    def test_tunnel_run(self):
        app = _hi_app()
        with patch("navig.commands.tunnel.start_tunnel") as fn:
            runner.invoke(app, ["tunnel", "run"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_tunnel_start_deprecated(self):
        app = _hi_app()
        with patch("navig.cli.host_infra.deprecation_warning") as dw, \
             patch("navig.commands.tunnel.start_tunnel") as fn:
            runner.invoke(app, ["tunnel", "start"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_tunnel_remove(self):
        app = _hi_app()
        with patch("navig.commands.tunnel.stop_tunnel") as fn:
            runner.invoke(app, ["tunnel", "remove"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_tunnel_stop_deprecated(self):
        app = _hi_app()
        with patch("navig.cli.host_infra.deprecation_warning") as dw, \
             patch("navig.commands.tunnel.stop_tunnel") as fn:
            runner.invoke(app, ["tunnel", "stop"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_tunnel_update(self):
        app = _hi_app()
        with patch("navig.commands.tunnel.restart_tunnel") as fn:
            runner.invoke(app, ["tunnel", "update"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_tunnel_restart_deprecated(self):
        app = _hi_app()
        with patch("navig.cli.host_infra.deprecation_warning") as dw, \
             patch("navig.commands.tunnel.restart_tunnel") as fn:
            runner.invoke(app, ["tunnel", "restart"], obj=dict(_OBJ))
        dw.assert_called_once()
        fn.assert_called_once()

    def test_tunnel_show(self):
        app = _hi_app()
        with patch("navig.commands.tunnel.show_tunnel_status") as fn:
            runner.invoke(app, ["tunnel", "show"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_tunnel_auto(self):
        app = _hi_app()
        with patch("navig.commands.tunnel.auto_tunnel") as fn:
            runner.invoke(app, ["tunnel", "auto"], obj=dict(_OBJ))
        fn.assert_called_once()


# ─── host_infra: monitor commands ────────────────────────────────────────────

class TestHostInfraMonitor:
    def test_monitor_show_default_health_check(self):
        app = _hi_app()
        with patch("navig.commands.monitoring.health_check") as fn:
            runner.invoke(app, ["monitor", "show"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_monitor_show_resources(self):
        app = _hi_app()
        with patch("navig.commands.monitoring.monitor_resources") as fn:
            runner.invoke(app, ["monitor", "show", "--resources"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_monitor_show_disk(self):
        app = _hi_app()
        with patch("navig.commands.monitoring.monitor_disk") as fn:
            runner.invoke(app, ["monitor", "show", "--disk"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_monitor_run_health_check(self):
        app = _hi_app()
        with patch("navig.commands.monitoring.health_check") as fn:
            runner.invoke(app, ["monitor", "run"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_monitor_report(self):
        app = _hi_app()
        with patch("navig.commands.monitoring.generate_report") as fn:
            runner.invoke(app, ["monitor", "report"], obj=dict(_OBJ))
        fn.assert_called_once()


# ─── host_infra: security commands ───────────────────────────────────────────

class TestHostInfraSecurity:
    def test_security_show_default_scan(self):
        app = _hi_app()
        with patch("navig.commands.security.security_scan") as fn:
            runner.invoke(app, ["security", "show"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_security_show_firewall(self):
        app = _hi_app()
        with patch("navig.commands.security.firewall_status") as fn:
            runner.invoke(app, ["security", "show", "--firewall"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_security_run(self):
        app = _hi_app()
        with patch("navig.commands.security.security_scan") as fn:
            runner.invoke(app, ["security", "run"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_security_updates(self):
        app = _hi_app()
        with patch("navig.commands.security.check_security_updates") as fn:
            runner.invoke(app, ["security", "updates"], obj=dict(_OBJ))
        fn.assert_called_once()

    def test_security_scan_cmd(self):
        app = _hi_app()
        with patch("navig.commands.security.security_scan") as fn:
            runner.invoke(app, ["security", "scan"], obj=dict(_OBJ))
        fn.assert_called_once()
