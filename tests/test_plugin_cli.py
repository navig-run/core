from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    # Force UTF-8 to prevent UnicodeDecodeError from Rich box-drawing characters
    # on non-UTF-8 Windows console encodings (e.g. cp1251).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def test_builtin_plugin_help_loads_command(tmp_path: Path) -> None:
    result = _run_cli("mini", "--help", home=tmp_path)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "NAVIG Mini" in combined
    assert "status" in combined


def test_plugin_info_accepts_canonical_command_name(tmp_path: Path) -> None:
    result = _run_cli("plugin", "info", "mini", home=tmp_path)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "Plugin: mini" in combined


def test_plugin_info_accepts_legacy_package_name(tmp_path: Path) -> None:
    result = _run_cli("plugin", "info", "navig_mini", home=tmp_path)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "Plugin: mini" in combined
