from unittest.mock import MagicMock, patch

from navig.commands import security


def _mock_config_manager():
    config_manager = MagicMock()
    config_manager.get_active_server.return_value = "prod"
    config_manager.load_server_config.return_value = {
        "host": "example.com",
        "user": "root",
    }
    return config_manager


@patch("navig.commands.security.get_config_manager")
@patch("navig.commands.security.RemoteOperations")
def test_firewall_add_rule_rejects_invalid_port(
    mock_remote_ops, mock_get_config_manager
):
    mock_get_config_manager.return_value = _mock_config_manager()

    security.firewall_add_rule("22; whoami", "tcp", "any", {})

    mock_remote_ops.return_value.execute_command.assert_not_called()


@patch("navig.commands.security.get_config_manager")
@patch("navig.commands.security.RemoteOperations")
def test_firewall_add_rule_rejects_invalid_allow_from(
    mock_remote_ops, mock_get_config_manager
):
    mock_get_config_manager.return_value = _mock_config_manager()

    security.firewall_add_rule(22, "tcp", "0.0.0.0/0; whoami", {})

    mock_remote_ops.return_value.execute_command.assert_not_called()


@patch("navig.commands.security.get_config_manager")
@patch("navig.commands.security.RemoteOperations")
def test_firewall_remove_rule_rejects_invalid_protocol(
    mock_remote_ops, mock_get_config_manager
):
    mock_get_config_manager.return_value = _mock_config_manager()

    security.firewall_remove_rule(22, "tcp; whoami", {})

    mock_remote_ops.return_value.execute_command.assert_not_called()


@patch("navig.commands.security.get_config_manager")
@patch("navig.commands.security.RemoteOperations")
def test_fail2ban_unban_rejects_invalid_jail_name(
    mock_remote_ops, mock_get_config_manager
):
    mock_get_config_manager.return_value = _mock_config_manager()

    security.fail2ban_unban("127.0.0.1", "sshd; whoami", {})

    mock_remote_ops.return_value.execute_command.assert_not_called()


@patch("navig.commands.security.get_config_manager")
@patch("navig.commands.security.RemoteOperations")
def test_fail2ban_unban_rejects_invalid_ip(mock_remote_ops, mock_get_config_manager):
    mock_get_config_manager.return_value = _mock_config_manager()

    security.fail2ban_unban("127.0.0.1; whoami", "sshd", {})

    mock_remote_ops.return_value.execute_command.assert_not_called()


@patch("navig.commands.security.get_config_manager")
@patch("navig.commands.security.RemoteOperations")
def test_fail2ban_unban_all_jails(mock_remote_ops, mock_get_config_manager):
    mock_get_config_manager.return_value = _mock_config_manager()
    remote_ops = mock_remote_ops.return_value
    remote_ops.execute_command.side_effect = [
        {"exit_code": 0, "stdout": "", "stderr": ""}
    ]

    security.fail2ban_unban("127.0.0.1", None, {})

    assert remote_ops.execute_command.call_args_list[0].args[0] == (
        "sudo fail2ban-client unban 127.0.0.1"
    )
