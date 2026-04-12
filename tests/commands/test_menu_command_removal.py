from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from navig.cli import app
import pytest

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


def test_menu_command_is_removed_from_cli_surface():
    result = runner.invoke(app, ["menu"])

    assert result.exit_code != 0
    assert "No such command" in result.output


def test_interactive_alias_is_removed_from_cli_surface():
    result = runner.invoke(app, ["interactive"])

    assert result.exit_code != 0
    assert "No such command" in result.output


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
