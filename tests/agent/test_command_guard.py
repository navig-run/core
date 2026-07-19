"""The agent command guard blocks never-legitimate catastrophic shell commands and honors
the ``executor.command_enabled`` kill-switch — without ever refusing a legitimate command.

This is defense-in-depth for the agent's ``command`` action (which runs LLM-generated shell
via subprocess). It is NOT a complete sandbox; see command_guard.py / PRODUCTION_AUDIT P-C.
"""

from __future__ import annotations

import pytest

from navig.agent.command_guard import CommandBlocked, guard_agent_command

# Never-legitimate: a real operator command never looks like any of these.
CATASTROPHIC = [
    "rm -rf /",
    "rm -rf /*",
    "rm -fr / ",
    "rm -rf / #wipe",
    "sudo rm -rf --no-preserve-root /",
    'rm -rf "/"',
    "rm -rf '/'",
    "dd if=/dev/zero of=/dev/sda bs=1M",
    "dd if=/dev/urandom of=/dev/nvme0n1",
    "mkfs.ext4 /dev/sdb1",
    "echo boom > /dev/sda",
    ":(){ :|:& };:",
]

# Must all PASS — the guard must not break normal agent usage.
LEGITIMATE = [
    "ls -la",
    "git status && git log --oneline -5",
    "echo hello world",
    "rm -rf /tmp/scratch",      # a real temp dir, not root
    "rm -rf ./build",
    "rm -rf node_modules",
    "rm -f /var/log/app.log",   # force, not recursive, not root
    "dd if=input.iso of=output.img bs=4M",   # file → file, not a device
    "grep -rf patterns.txt .",  # 'grep', not 'rm'; -rf flags are innocuous here
    "python -c \"print('ok')\"",
    "systemctl restart navig",
]


@pytest.mark.parametrize("cmd", CATASTROPHIC)
def test_catastrophic_commands_are_blocked(cmd):
    with pytest.raises(CommandBlocked):
        guard_agent_command(cmd)


@pytest.mark.parametrize("cmd", LEGITIMATE)
def test_legitimate_commands_pass(cmd):
    guard_agent_command(cmd)  # must not raise


def test_empty_command_is_a_noop():
    guard_agent_command("")
    guard_agent_command("   ")


class _FakeCM:
    def __init__(self, executor: dict):
        self.global_config = {"executor": executor}


def test_kill_switch_blocks_everything(monkeypatch):
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM({"command_enabled": False}))
    with pytest.raises(CommandBlocked):
        guard_agent_command("ls")


def test_kill_switch_string_false_is_honored(monkeypatch):
    # `navig config set executor.command_enabled false` stores the RAW string "false".
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM({"command_enabled": "false"}))
    with pytest.raises(CommandBlocked):
        guard_agent_command("ls")


def test_enabled_by_default_when_unset(monkeypatch):
    monkeypatch.setattr("navig.config.get_config_manager", lambda: _FakeCM({}))
    guard_agent_command("echo ok")  # must not raise


def test_a_config_error_fails_open_not_closed(monkeypatch):
    """A config read hiccup must not brick the agent — the kill-switch defaults ON (enabled)."""
    def _boom():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr("navig.config.get_config_manager", _boom)
    guard_agent_command("echo still works")  # must not raise
