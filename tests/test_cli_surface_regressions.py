from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent


def _cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    return env


def _run_cli(args: list[str], *, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=_cli_env(tmp_path),
    )


def test_gateway_session_handles_missing_gateway_without_invalid_url(tmp_path: Path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir()
    (navig_dir / "config.yaml").write_text("gateway:\n  port: 58789\n")
    result = _run_cli(["gateway", "session", "list"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "Invalid URL" not in combined
    assert "Gateway is not running" in combined


def test_heartbeat_status_handles_missing_gateway_without_invalid_url(tmp_path: Path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(exist_ok=True)
    (navig_dir / "config.yaml").write_text("gateway:\n  port: 58789\n")
    result = _run_cli(["heartbeat", "status"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "Invalid URL" not in combined
    assert "Gateway is not running" in combined


def test_browser_help_command_is_available(tmp_path: Path):
    result = _run_cli(["browser", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "No such command 'browser'" not in combined
    assert "Browser automation" in combined


def test_mesh_help_command_is_available(tmp_path: Path):
    result = _run_cli(["mesh", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "No such command 'mesh'" not in combined
    assert "Mesh topology management" in combined


def test_bot_start_uses_configured_gateway_port_when_unspecified(monkeypatch):
    import navig.cli as cli

    recorded: dict[str, list[str]] = {}

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(cli, "_load_gateway_cli_defaults", lambda: (8789, "127.0.0.1"))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: recorded.setdefault("cmd", cmd) or SimpleNamespace(),
    )

    cli.bot_start(gateway=True, port=None, background=True)

    assert recorded["cmd"][-1] == "8789"
    assert "None" not in recorded["cmd"]


def test_quick_start_uses_configured_gateway_port_when_unspecified(monkeypatch):
    import navig.cli as cli

    recorded: dict[str, list[str]] = {}

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(cli, "_load_gateway_cli_defaults", lambda: (8789, "127.0.0.1"))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: recorded.setdefault("cmd", cmd) or SimpleNamespace(),
    )

    cli.quick_start(bot=True, gateway=True, port=None, background=True)

    assert recorded["cmd"][-1] == "8789"
    assert "None" not in recorded["cmd"]


def test_gitignore_is_text_and_covers_rust_targets():
    data = (ROOT / ".gitignore").read_bytes()

    assert b"\x00" not in data
    assert b"**/target/" in data


def test_deploy_template_cmd_accepts_named_template_commands(monkeypatch):
    import navig.commands.template as template_mod

    template = SimpleNamespace(get_commands=lambda: [{"name": "status", "command": "echo USER"}])
    manager = SimpleNamespace(
        discover_templates=lambda: None,
        get_template=lambda name: template,
    )
    called: dict[str, object] = {}

    monkeypatch.setattr(template_mod, "TemplateManager", lambda: manager)
    monkeypatch.setitem(
        sys.modules,
        "navig.commands.remote",
        SimpleNamespace(
            run_remote_command=lambda command, options: called.update(
                {"command": command, "options": options}
            )
        ),
    )

    template_mod.deploy_template_cmd(
        "demo",
        command_name="status",
        command_args=["alice"],
        ctx_obj={},
    )

    assert called["command"] == "echo alice"
    assert called["options"]["dry_run"] is False


def test_addon_help_starts_without_duplicate_run_registration(tmp_path: Path):
    result = _run_cli(["addon", "--help"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "addon" in combined.lower()


def test_config_cache_bypass_forces_fresh_instances(tmp_path: Path):
    import navig.config as config_mod

    config_mod.reset_config_manager()
    config_mod.set_config_cache_bypass(False)

    first = config_mod.get_config_manager(config_dir=tmp_path / "cfg")
    second = config_mod.get_config_manager(config_dir=tmp_path / "cfg")
    assert first is second

    config_mod.set_config_cache_bypass(True)
    fresh_one = config_mod.get_config_manager(config_dir=tmp_path / "cfg")
    fresh_two = config_mod.get_config_manager(config_dir=tmp_path / "cfg")
    assert fresh_one is not fresh_two

    config_mod.set_config_cache_bypass(False)
    config_mod.reset_config_manager()
