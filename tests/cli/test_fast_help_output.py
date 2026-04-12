from __future__ import annotations

import json
import re

from navig.main import _fast_help_text
import pytest

pytestmark = pytest.mark.unit


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


def test_builtin_commands_do_not_include_removed_legacy_names():
    import navig.main as main_mod

    assert "explain" not in main_mod._BUILTIN_COMMANDS
    assert "monitor" not in main_mod._BUILTIN_COMMANDS
    assert "security" not in main_mod._BUILTIN_COMMANDS
    assert "workflow" not in main_mod._BUILTIN_COMMANDS
    assert "template" not in main_mod._BUILTIN_COMMANDS
    assert "hestia" not in main_mod._BUILTIN_COMMANDS


def test_should_skip_plugin_loading_does_not_skip_unknown_when_cache_missing_entry(
    tmp_path,
    monkeypatch,
):
    import navig.main as main_mod

    cache_data = {
        "plugins": {
            "sample-plugin": {
                "name": "sample-plugin",
                "path": str(tmp_path / "plugins" / "sample-plugin"),
            }
        }
    }
    cache_file = tmp_path / "plugins_cache.json"
    cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

    monkeypatch.setattr(main_mod.paths, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(main_mod.paths, "config_dir", lambda: tmp_path)

    should_skip = main_mod._should_skip_plugin_loading(["navig", "brand-new-command"])
    assert should_skip is False


def test_should_skip_plugin_loading_respects_builtin_after_global_flags():
    import navig.main as main_mod

    should_skip = main_mod._should_skip_plugin_loading([
        "navig",
        "--host",
        "prod",
        "host",
        "list",
    ])
    assert should_skip is True


def test_should_skip_plugin_loading_keeps_plugin_command_after_global_flags():
    import navig.main as main_mod

    should_skip = main_mod._should_skip_plugin_loading([
        "navig",
        "--host",
        "prod",
        "plugin",
        "list",
    ])
    assert should_skip is False


def test_should_skip_plugin_loading_for_help_with_topic():
    import navig.main as main_mod

    should_skip = main_mod._should_skip_plugin_loading(["navig", "help", "host"])
    assert should_skip is True


def test_main_does_not_duplicate_profile_registration(monkeypatch):
    import navig.cli as cli_mod
    import navig.main as main_mod

    class _DummyApp:
        def __init__(self):
            self.added_names: list[str | None] = []

        def add_typer(self, _sub_app, name=None, hidden=False):
            self.added_names.append(name)

        def __call__(self):
            return None

    dummy_app = _DummyApp()

    def _fake_register_external_commands():
        dummy_app.add_typer(object(), name="profile")

    monkeypatch.setattr(main_mod.sys, "argv", ["navig", "profile", "show"])
    monkeypatch.setattr(main_mod, "_normalize_help_compat_args", lambda argv: argv)
    monkeypatch.setattr(main_mod, "_maybe_handle_fast_path", lambda argv: False)
    monkeypatch.setattr(main_mod, "_check_first_run", lambda: None)
    monkeypatch.setattr(main_mod, "_should_skip_plugin_loading", lambda argv: True)

    monkeypatch.setattr("navig.config.set_config_cache_bypass", lambda _value: None)
    monkeypatch.setattr("navig.config.reset_config_manager", lambda: None)

    monkeypatch.setattr(
        "navig.config.get_config_manager",
        lambda: type("_Cfg", (), {"global_config_dir": "."})(),
    )
    monkeypatch.setattr(
        "navig.migrations.workspace_to_spaces.migrate_workspace_to_spaces",
        lambda _path: None,
    )
    monkeypatch.setattr(
        "navig.migrations.workspace_to_spaces.ensure_no_stale_spaces_registration",
        lambda: None,
    )

    monkeypatch.setattr(cli_mod, "app", dummy_app)
    monkeypatch.setattr(cli_mod, "_register_external_commands", _fake_register_external_commands)

    main_mod.main()

    assert dummy_app.added_names.count("profile") == 1
