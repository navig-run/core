from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    # Ensure deterministic launcher behavior in subprocess tests even when the
    # outer shell exports NAVIG_LAUNCHER=legacy.
    env["NAVIG_LAUNCHER"] = "fuzzy"
    # Force UTF-8 encoding so Rich's box-drawing characters don't cause a
    # UnicodeDecodeError when the system locale is non-UTF-8 (e.g. cp1251).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(args: list[str], *, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
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


@pytest.mark.parametrize(
    "domain",
    [
        "host",
        "db",
        "file",
        "app",
        "docker",
        "log",
        "wiki",
        "mode",
        "matrix",
        "plans",
        "agent",
        "space",
    ],
)
def test_domain_launcher_non_tty_exits_cleanly_with_hint(tmp_path: Path, domain: str):
    """Domain launchers should not hang/crash in non-interactive subprocesses.

    In non-TTY contexts, smart_launch must print the explicit help hint and
    exit with status 0 for each supported launcher domain.
    """
    result = subprocess.run(
        [sys.executable, "-m", "navig", domain],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "non-tty detected" in combined.lower()
    assert f"navig {domain} --help" in combined.lower()


def test_task_non_tty_lists_workflows_without_launcher_hint(tmp_path: Path):
    """`navig task` uses direct listing behavior in non-TTY mode.

    This command currently does not route through the launcher fallback hint
    path; it should still exit cleanly and render its workflow table.
    """
    result = subprocess.run(
        [sys.executable, "-m", "navig", "task"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "available workflows" in combined.lower()
    assert "non-tty detected" not in combined.lower()


def test_bot_start_uses_configured_gateway_port_when_unspecified(monkeypatch):
    import navig.commands.gateway as gw_mod

    recorded: dict[str, list[str]] = {}

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(gw_mod, "_load_gateway_cli_defaults", lambda: (8789, "127.0.0.1"))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: recorded.setdefault("cmd", cmd) or SimpleNamespace(),
    )

    gw_mod.bot_start(gateway=True, port=None, background=True)

    assert recorded["cmd"][-1] == "8789"
    assert "None" not in recorded["cmd"]


def test_quick_start_uses_configured_gateway_port_when_unspecified(monkeypatch):
    import navig.cli as cli
    import navig.commands.gateway as gw_mod

    recorded: dict[str, list[str]] = {}

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setattr(gw_mod, "_load_gateway_cli_defaults", lambda: (8789, "127.0.0.1"))
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


# test_addon_help_starts_without_duplicate_run_registration removed:
# addon_app was a deprecated hidden group removed in the inline cleanup.
# Canonical replacement: navig flow template.


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
