"""`navig host security …` read the SSH result as a dict. It is a CompletedProcess.

`RemoteOperations.execute_command()` returns a ``subprocess.CompletedProcess``. Every
command in ``commands/security.py`` treated it as a mapping::

    result = remote_ops.execute_command("sudo ufw status verbose", server_config)
    if result["exit_code"] != 0:            # TypeError: not subscriptable
        … result.get('stderr', 'Unknown error')   # AttributeError: no attribute 'get'

33 such reads across every subcommand — firewall status/add/remove/enable/disable,
fail2ban status and unban, the SSH audit, the update check and the full security scan.
The first one executes immediately after the SSH call succeeds, so each command ran its
remote command and then crashed while reporting the result.

Why the existing suite stayed green: ``tests/security/test_security_commands.py`` patches
``RemoteOperations`` with a plain ``MagicMock``, and ``MagicMock()["exit_code"]`` returns
another MagicMock quite happily — the dict access "works". Every one of those tests also
asserts ``execute_command.assert_not_called()``, because they cover the *validation*
rejections only. The success path had never been executed by anything.

These tests return a real ``CompletedProcess``, so the result contract is real.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from navig.commands import security

UFW_STATUS = """Status: active
Logging: on (low)
Default: deny (incoming), allow (outgoing), disabled (routed)

To                         Action      From
--                         ------      ----
22/tcp                     ALLOW       Anywhere
443/tcp                    ALLOW       Anywhere
"""


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def remote():
    """Patch the boundary, but return the REAL result type these commands must handle."""
    ops = MagicMock()
    ops.execute_command.return_value = _completed(0, stdout=UFW_STATUS)
    cm = MagicMock()
    cm.get_active_server.return_value = "prod"
    cm.load_server_config.return_value = {"host": "example.com", "user": "root"}
    with patch("navig.commands.security.RemoteOperations", return_value=ops), patch(
        "navig.commands.security.get_config_manager", return_value=cm
    ), patch("navig.commands.security.require_active_server", return_value="prod"):
        yield ops


def test_firewall_status_renders_the_rules(remote, capsys) -> None:
    security.firewall_status({})

    out = capsys.readouterr().out
    assert "22/tcp" in out
    assert "Total rules" in out


def test_firewall_status_json_mode_emits_parseable_json(remote, capsys) -> None:
    import json

    security.firewall_status({"json_output": True})

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "active"
    assert any("22/tcp" in rule for rule in payload["rules"])


def test_firewall_status_reports_a_failed_command(remote, capsys) -> None:
    """The failure branch read result.get('stderr', …) — CompletedProcess has no .get."""
    remote.execute_command.return_value = _completed(1, stderr="ufw: command not found")

    security.firewall_status({})

    assert "ufw: command not found" in capsys.readouterr().out


def test_firewall_add_rule_reports_success(remote, capsys) -> None:
    remote.execute_command.return_value = _completed(0, stdout="Rule added")

    security.firewall_add_rule(8080, "tcp", "any", {})

    assert "added successfully" in capsys.readouterr().out


def test_firewall_add_rule_reports_failure_with_stderr(remote, capsys) -> None:
    remote.execute_command.return_value = _completed(1, stderr="ERROR: Bad port")

    security.firewall_add_rule(8080, "tcp", "any", {})

    out = capsys.readouterr().out
    assert "Failed to add firewall rule" in out
    assert "ERROR: Bad port" in out


def test_firewall_remove_rule_reports_success(remote, capsys) -> None:
    remote.execute_command.return_value = _completed(0, stdout="Rule deleted")

    security.firewall_remove_rule(8080, "tcp", {})

    assert "removed successfully" in capsys.readouterr().out


def test_a_none_stdout_does_not_crash_the_renderer(remote, capsys) -> None:
    """capture_output=False yields stdout=None — `.strip()` on None would raise."""
    remote.execute_command.return_value = _completed(0, stdout=None, stderr=None)

    security.firewall_status({})

    capsys.readouterr()  # must simply not raise


def test_execute_command_really_returns_a_completed_process() -> None:
    """Pin the premise this whole module depends on."""
    import inspect

    from navig.remote import RemoteOperations

    assert (
        inspect.signature(RemoteOperations.execute_command).return_annotation
        is subprocess.CompletedProcess
    )
    assert not hasattr(subprocess.CompletedProcess, "get")
    with pytest.raises(TypeError):
        _completed()["exit_code"]
