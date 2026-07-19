from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.cli import app

pytestmark = pytest.mark.integration

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent.parent


def _cli_env(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["NAVIG_SKIP_ONBOARDING"] = "1"
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


def test_the_legacy_interactive_menu_stays_gone():
    """The LEGACY interactive menu must not come back as a command.

    This file used to assert that `navig menu` does not exist at all — and it has
    been red ever since, because `navig menu` DOES exist now and is supposed to:
    it is the launcher for the navig-menu project-menu engine
    (navig/commands/menu.py), registered on purpose. Same word, different feature.

    What must stay retired is the old interactive shell — `navig interactive`,
    and `navig menu` routing INTO it. So assert that, instead of a falsehood:
    a stale test that can never pass just hides the real regressions around it.
    """
    result = runner.invoke(app, ["interactive"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_menu_is_the_launcher_not_the_old_interactive_shell():
    from navig.cli import registration as reg

    target = reg._EXTERNAL_CMD_MAP.get("menu")
    assert target is not None, "`navig menu` (the navig-menu launcher) should be registered"
    module, _attr = target
    assert module == "navig.commands.menu", (
        f"`navig menu` must route to the menu-builder launcher, not {module} — "
        "the legacy interactive shell is retired"
    )
    assert "interactive" not in reg._EXTERNAL_CMD_MAP


def test_core_standalone_groups_remain_available(tmp_path: Path):
    for cmd in (
        ["host", "--help"],
        ["file", "--help"],
        ["db", "--help"],
        ["web", "--help"],
        ["docker", "--help"],
        ["flow", "--help"],
        ["local", "--help"],
        ["mcp", "--help"],
        ["wiki", "--help"],
        ["backup", "--help"],
        ["config", "--help"],
    ):
        result = _run_cli(cmd, tmp_path=tmp_path)
        combined = result.stdout + result.stderr
        assert result.returncode == 0, f"{' '.join(cmd)} failed: {combined}"
