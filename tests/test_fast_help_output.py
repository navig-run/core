from __future__ import annotations

import re

from navig.main import _fast_help_text


def test_fast_help_has_required_sections():
    text = _fast_help_text("9.9.9")
    for section in (
        "  CORE",
        "  CONNECTIONS",
        "  APPS & SERVICES",
        "  INFRASTRUCTURE",
        "  SECURITY",
        "  ENVIRONMENT",
        "  MONITORING",
        "  DEVELOPER",
        "  EXAMPLES",
    ):
        assert section in text


def test_fast_help_includes_requested_commands():
    text = _fast_help_text("9.9.9")
    for command in (
        "logs",
        "stats",
        "health",
        "cert",
        "key",
        "firewall",
        "dns",
        "port",
        "proxy",
        "env",
        "secret",
        "cron",
        "job",
        "upgrade",
        "plugin",
        "alias",
        "script",
        "config",
        "version",
        "status",
    ):
        assert f"    {command}" in text


def test_fast_help_alignment_and_width():
    text = _fast_help_text("9.9.9")
    lines = text.splitlines()

    assert all(len(line) <= 80 for line in lines)

    command_lines = [
        line
        for line in lines
        if re.match(r"^\s{4}[a-z][a-z\-\s&]*\s{2,}\S", line)
        and not line.strip().startswith("navig ")
    ]
    assert command_lines

    desc_columns = []
    for line in command_lines:
        command_block = line[4:17]
        assert len(command_block) == 13
        desc_columns.append(17)

    assert len(set(desc_columns)) == 1


def test_fast_help_status_bar_uses_env(monkeypatch):
    monkeypatch.setenv("NAVIG_ACTIVE_HOST", "staging-01")
    monkeypatch.setenv("NAVIG_PROFILE", "operator")

    text = _fast_help_text("2.4.21")
    assert "NAVIG v2.4.21" in text
    assert "host: staging-01" in text
    assert "profile: operator" in text


def test_external_command_map_includes_server_aliases():
    import navig.cli as cli_mod

    for command_name in (
        "logs",
        "stats",
        "health",
        "cert",
        "key",
        "firewall",
        "dns",
        "port",
        "proxy",
        "env",
        "secret",
        "cron",
        "job",
        "upgrade",
        "plugin",
        "alias",
        "script",
        "config",
        "version",
        "status",
    ):
        assert command_name in cli_mod._EXTERNAL_CMD_MAP or command_name in {
            "upgrade",
            "plugin",
            "config",
            "version",
            "status",
        }
