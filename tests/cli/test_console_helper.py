"""Hermetic unit tests for pure functions in navig.console_helper."""

from __future__ import annotations

import pytest

from navig.console_helper import (
    CONFIRMATION_THRESHOLDS,
    OPERATION_LEVELS,
    Colors,
    classify_command,
    classify_sql,
    requires_confirmation,
)

# ---------------------------------------------------------------------------
# Colors constants
# ---------------------------------------------------------------------------


class TestColors:
    def test_success_is_green(self):
        assert Colors.SUCCESS == "green"

    def test_error_is_red(self):
        assert Colors.ERROR == "red"

    def test_warning_is_yellow(self):
        assert Colors.WARNING == "yellow"

    def test_info_is_blue(self):
        assert Colors.INFO == "blue"


# ---------------------------------------------------------------------------
# OPERATION_LEVELS and CONFIRMATION_THRESHOLDS tables
# ---------------------------------------------------------------------------


class TestOperationLevels:
    def test_critical_is_lowest_number(self):
        assert OPERATION_LEVELS["critical"] < OPERATION_LEVELS["standard"]
        assert OPERATION_LEVELS["standard"] < OPERATION_LEVELS["verbose"]

    def test_all_three_levels_present(self):
        assert set(OPERATION_LEVELS.keys()) >= {"critical", "standard", "verbose"}

    def test_thresholds_match_levels(self):
        assert CONFIRMATION_THRESHOLDS["critical"] == 1
        assert CONFIRMATION_THRESHOLDS["standard"] == 2
        assert CONFIRMATION_THRESHOLDS["verbose"] == 3


# ---------------------------------------------------------------------------
# requires_confirmation
# ---------------------------------------------------------------------------


class TestRequiresConfirmation:
    """Pure logic: no console, no config — all booleans."""

    def test_auto_confirm_flag_bypasses_all(self):
        for op_type in ("critical", "standard", "verbose"):
            assert (
                requires_confirmation(op_type, "verbose", "interactive", auto_confirm=True) is False
            )

    def test_auto_execution_mode_bypasses_all(self):
        for op_type in ("critical", "standard", "verbose"):
            assert (
                requires_confirmation(op_type, "verbose", "auto", auto_confirm=False) is False
            )

    def test_verbose_level_requires_all_op_types(self):
        for op_type in ("critical", "standard", "verbose"):
            assert (
                requires_confirmation(op_type, "verbose", "interactive") is True
            ), f"verbose level should confirm {op_type}"

    def test_standard_level_skips_verbose_ops(self):
        assert requires_confirmation("verbose", "standard", "interactive") is False

    def test_standard_level_confirms_standard_and_critical(self):
        assert requires_confirmation("standard", "standard", "interactive") is True
        assert requires_confirmation("critical", "standard", "interactive") is True

    def test_critical_level_only_confirms_critical_ops(self):
        assert requires_confirmation("critical", "critical", "interactive") is True
        assert requires_confirmation("standard", "critical", "interactive") is False
        assert requires_confirmation("verbose", "critical", "interactive") is False

    def test_unknown_op_type_defaults_to_standard_level(self):
        # Default op level for unknown type is 2 (standard)
        # With standard threshold (2): 2 <= 2 → True
        assert requires_confirmation("unknown_op", "standard", "interactive") is True

    def test_unknown_confirmation_level_defaults_to_standard_threshold(self):
        # Default threshold for unknown level is 2 (standard)
        # critical op level is 1: 1 <= 2 → True
        assert requires_confirmation("critical", "unknown_level", "interactive") is True


# ---------------------------------------------------------------------------
# classify_command
# ---------------------------------------------------------------------------


class TestClassifyCommand:
    def test_rm_rf_is_critical(self):
        assert classify_command("rm -rf /tmp/dir") == "critical"

    def test_rmdir_is_critical(self):
        assert classify_command("rmdir old_dir") == "critical"

    def test_shutdown_is_critical(self):
        assert classify_command("shutdown -h now") == "critical"

    def test_drop_table_is_critical(self):
        assert classify_command("drop table users") == "critical"

    def test_delete_from_is_critical(self):
        assert classify_command("delete from orders") == "critical"

    def test_systemctl_stop_is_critical(self):
        assert classify_command("systemctl stop nginx") == "critical"

    def test_reboot_is_critical(self):
        assert classify_command("reboot") == "critical"

    def test_chmod_is_standard(self):
        assert classify_command("chmod 755 script.sh") == "standard"

    def test_apt_install_is_standard(self):
        assert classify_command("apt install git") == "standard"

    def test_git_push_is_standard(self):
        assert classify_command("git push origin main") == "standard"

    def test_systemctl_restart_is_standard(self):
        assert classify_command("systemctl restart nginx") == "standard"

    def test_pip_install_is_standard(self):
        assert classify_command("pip install requests") == "standard"

    def test_plain_ls_is_verbose(self):
        assert classify_command("ls -la /tmp") == "verbose"

    def test_ls_is_verbose(self):
        assert classify_command("ls /tmp") == "verbose"

    def test_grep_is_verbose(self):
        assert classify_command("grep -r error /var/log") == "verbose"

    def test_case_insensitive_rm(self):
        assert classify_command("RM -RF /tmp") == "critical"

    def test_empty_command_is_verbose(self):
        assert classify_command("") == "verbose"


# ---------------------------------------------------------------------------
# classify_sql
# ---------------------------------------------------------------------------


class TestClassifySql:
    def test_drop_table_is_critical(self):
        assert classify_sql("DROP TABLE users") == "critical"

    def test_truncate_is_critical(self):
        assert classify_sql("TRUNCATE users") == "critical"

    def test_delete_is_critical(self):
        assert classify_sql("DELETE FROM orders WHERE id=1") == "critical"

    def test_create_table_is_standard(self):
        assert classify_sql("CREATE TABLE accounts (id INT)") == "standard"

    def test_alter_table_is_standard(self):
        assert classify_sql("ALTER TABLE users ADD COLUMN email VARCHAR(255)") == "standard"

    def test_insert_is_standard(self):
        assert classify_sql("INSERT INTO users VALUES (1, 'bob')") == "standard"

    def test_update_is_standard(self):
        assert classify_sql("UPDATE users SET name='alice' WHERE id=1") == "standard"

    def test_grant_is_standard(self):
        assert classify_sql("GRANT SELECT ON db.* TO 'user'@'localhost'") == "standard"

    def test_revoke_is_standard(self):
        assert classify_sql("REVOKE ALL PRIVILEGES FROM 'user'@'localhost'") == "standard"

    def test_select_is_verbose(self):
        assert classify_sql("SELECT * FROM users") == "verbose"

    def test_show_tables_is_verbose(self):
        assert classify_sql("SHOW TABLES") == "verbose"

    def test_describe_is_verbose(self):
        assert classify_sql("DESCRIBE users") == "verbose"

    def test_lowercase_drop_is_critical(self):
        assert classify_sql("drop table users") == "critical"

    def test_empty_query_is_verbose(self):
        assert classify_sql("") == "verbose"
