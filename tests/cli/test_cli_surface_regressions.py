from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent


def _cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    # `conftest` exports NAVIG_CONFIG_DIR/NAVIG_DATA_DIR to isolate THIS process, and
    # `os.environ.copy()` hands them to the subprocess — where they outrank HOME, since
    # `paths.config_dir()` checks the env var first. So the `config.yaml` these tests
    # write into tmp_path was never read: the CLI resolved the conftest dir, found no
    # gateway config, fell back to the DEFAULT port, and talked to the operator's live
    # daemon. The tests passed because a real gateway answered 200 and the assertion
    # list tolerates a real answer — they were exercising the machine, not the tmp dir.
    # Pinning both here makes the config the test wrote the config the CLI reads, and
    # removes the live daemon from the picture entirely.
    env["NAVIG_CONFIG_DIR"] = str(tmp_path / ".navig")
    env["NAVIG_DATA_DIR"] = str(tmp_path / ".navig" / "data")
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    # Ensure deterministic launcher behavior in subprocess tests even when the
    # outer shell exports NAVIG_LAUNCHER=legacy.
    env["NAVIG_LAUNCHER"] = "fuzzy"
    # Force UTF-8 encoding so Rich's box-drawing characters don't cause a
    # UnicodeDecodeError when the system locale is non-UTF-8 (e.g. cp1251).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_cli(
    args: list[str], *, tmp_path: Path, timeout: int = 30
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=timeout,
    )


def test_gateway_session_handles_missing_gateway_without_invalid_url(tmp_path: Path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir()
    (navig_dir / "config.yaml").write_text("gateway:\n  port: 58789\n")
    result = _run_cli(["gateway", "session", "list"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    # The regression this test exists for is the "Invalid URL" CRASH (asserted just
    # below); the exit code was only ever incidental to it. `gateway session` now exits
    # 1 when it cannot REACH the gateway — an unreachable daemon is not an empty answer
    # — and whether this run reaches one depends on the machine (the comment below says
    # as much), so both codes are legitimate here.
    assert result.returncode in (0, 1), combined
    assert "Invalid URL" not in combined
    # A crash also exits 1, so pin that difference explicitly rather than let the
    # widened code above hide one.
    assert "Traceback" not in combined, combined
    # `_cli_env` now pins NAVIG_CONFIG_DIR at tmp_path, so there is definitively no
    # gateway to reach and the graceful branch is the ONLY correct outcome. The list
    # below used to also accept a real answer, because these tests could not guarantee
    # the machine had no live daemon — and they did in fact reach the operator's, which
    # is how they kept passing while reading nothing they had written.
    assert any(
        s in combined
        for s in (
            "Gateway is not running", "not running", "No active sessions", "Start gateway",
            "Active sessions", "Heartbeat is", "Interval:",
        )
    ), combined
    assert "not running" in combined, (
        "the CLI reached SOMETHING; this test is meant to exercise the no-gateway path"
    )


def test_heartbeat_status_handles_missing_gateway_without_invalid_url(tmp_path: Path):
    navig_dir = tmp_path / ".navig"
    navig_dir.mkdir(exist_ok=True)
    (navig_dir / "config.yaml").write_text("gateway:\n  port: 58789\n")
    result = _run_cli(["heartbeat", "status"], tmp_path=tmp_path)
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "Invalid URL" not in combined
    # `_cli_env` now pins NAVIG_CONFIG_DIR at tmp_path, so there is definitively no
    # gateway to reach and the graceful branch is the ONLY correct outcome. The list
    # below used to also accept a real answer, because these tests could not guarantee
    # the machine had no live daemon — and they did in fact reach the operator's, which
    # is how they kept passing while reading nothing they had written.
    assert any(
        s in combined
        for s in (
            "Gateway is not running", "not running", "No active sessions", "Start gateway",
            "Active sessions", "Heartbeat is", "Interval:",
        )
    ), combined
    assert "not running" in combined, (
        "the CLI reached SOMETHING; this test is meant to exercise the no-gateway path"
    )


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
        "server",
        "inbox",
        "council",
        "cron",
        "flux",
        "formation",
        "migrate",
        "mount",
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
        timeout=20,
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    assert "non-tty detected" in combined.lower()
    assert f"navig {domain} --help" in combined.lower()


def test_task_non_tty_exits_cleanly_without_launcher_hint(tmp_path: Path):
    """`navig task` uses direct listing behavior in non-TTY mode.

    The workflow engine has been RETIRED — Blocks replaced it — so `navig task`
    no longer renders a workflow table; it points at `navig block list` instead.
    What this regression still guards is unchanged: the command must exit cleanly
    in a non-TTY and must NOT route through the interactive launcher fallback
    (the "non-tty detected" hint), which would hang or confuse a scripted caller.
    """
    result = subprocess.run(
        [sys.executable, "-m", "navig", "task"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_cli_env(tmp_path),
        stdin=subprocess.DEVNULL,
        timeout=20,
    )
    combined = result.stdout + result.stderr

    assert result.returncode == 0
    # Points the user at the replacement rather than dead-ending.
    assert "block" in combined.lower()
    assert "non-tty detected" not in combined.lower()


def test_bot_start_uses_configured_gateway_port_when_unspecified(monkeypatch):
    import navig.commands.gateway as gw_mod
    import navig.messaging.secrets as _secrets_mod

    recorded: dict[str, list[str]] = {}

    # Bypass vault check (SQLite lock can hang indefinitely in test environments)
    monkeypatch.setattr(_secrets_mod, "resolve_telegram_bot_token", lambda *a, **kw: "fake-token")
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
    import navig.messaging.secrets as _secrets_mod

    recorded: dict[str, list[str]] = {}

    # Bypass vault check (SQLite lock can hang indefinitely in test environments)
    monkeypatch.setattr(_secrets_mod, "resolve_telegram_bot_token", lambda *a, **kw: "fake-token")
    monkeypatch.setattr(gw_mod, "_load_gateway_cli_defaults", lambda: (8789, "127.0.0.1"))
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda cmd, **kwargs: recorded.setdefault("cmd", cmd) or SimpleNamespace(),
    )

    cli.quick_start(bot=True, gateway=True, port=None, background=True)

    assert recorded["cmd"][-1] == "8789"
    assert "None" not in recorded["cmd"]


def test_gateway_start_uses_cli_defaults_when_unspecified(monkeypatch):
    """`gateway start` with no --port/--host must take them from config.

    The single-instance sweep is stubbed, and that is not incidental: real
    `gateway_start` calls `_free_port(port)` and `_supersede_other_gateways()`
    before it builds anything, and the latter enumerates EVERY process on the
    machine and reads its environment to compare config dirs. For an assertion
    about default resolution that is both irrelevant and expensive — it is why
    this test blew the pre-push gate's 120s timeout under `-n auto`, which pytest
    reports as `worker 'gwNN' crashed` rather than as a slow test.

    The stubs assert they were CALLED rather than silently no-op, so a refactor
    that dropped the single-instance guarantee still fails here. The sweep's own
    behaviour is covered where it belongs: tests/gateway/test_free_port_scoping.py
    and tests/quality/test_no_unscoped_process_kill.py.
    """
    # `gateway start` installs a logging StreamHandler on the ROOT logger. Left behind,
    # every later test in this xdist worker gets duplicated log output through it -- and a
    # test asserting on captured output then sees each line twice. Restore the handler list.
    _root_handlers = list(logging.getLogger().handlers)
    # The code under test sets NAVIG_NO_NARRATOR directly, so hand the key to monkeypatch or
    # it survives teardown into every later test in this xdist worker.
    monkeypatch.setenv("NAVIG_NO_NARRATOR", "")
    import navig.commands.gateway as gw_mod

    captured: dict[str, object] = {}
    swept: list[str] = []

    class _DummyGatewayConfig:
        def __init__(self, raw_config):
            captured["raw_config"] = raw_config

    class _DummyNavigGateway:
        def __init__(self, config):
            captured["config"] = config

        async def start(self):
            return None

    monkeypatch.setattr(gw_mod, "_load_gateway_cli_defaults", lambda: (9911, "127.0.0.9"))
    monkeypatch.setattr(gw_mod, "_free_port", lambda port: swept.append(f"free_port:{port}"))
    monkeypatch.setattr(
        gw_mod, "_supersede_other_gateways", lambda: swept.append("supersede")
    )
    monkeypatch.setitem(
        sys.modules,
        "navig.gateway",
        SimpleNamespace(
            GatewayConfig=_DummyGatewayConfig,
            NavigGateway=_DummyNavigGateway,
        ),
    )
    monkeypatch.setattr("asyncio.run", lambda coro: coro.close())

    gw_mod.gateway_start(port=None, host=None, background=False)

    assert captured["raw_config"] == {
        "gateway": {
            "enabled": True,
            "port": 9911,
            "host": "127.0.0.9",
        }
    }
    # Startup still claims single-instance ownership — and on the resolved port,
    # not the None it was given.
    assert swept == ["free_port:9911", "supersede"]

    logging.getLogger().handlers[:] = _root_handlers


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
