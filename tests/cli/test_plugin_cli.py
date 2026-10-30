from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _run_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    # The session-wide NAVIG_CONFIG_DIR (core/tests/conftest.py) is INHERITED by
    # `os.environ.copy()` and WINS over HOME in `platform.paths.config_dir`, so
    # without this the CLI reads a config this test never wrote — and falls back to
    # the default port, where the operator's LIVE daemon answers. That is how
    # `test_gateway_session_handles_missing_gateway_without_invalid_url` came to
    # fail with HTTP 401 once #994 required a bearer token.
    env["NAVIG_CONFIG_DIR"] = str(home / ".navig")
    env["NAVIG_DATA_DIR"] = str(home / ".navig" / "data")
    env["NAVIG_SKIP_ONBOARDING"] = "1"
    # Force UTF-8 to prevent UnicodeDecodeError from Rich box-drawing characters
    # on non-UTF-8 Windows console encodings (e.g. cp1251).
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Run from `home`, never `cwd=REPO_ROOT`: conftest's app-root suppression is an
    # in-process patch a fresh interpreter does not inherit, so from core/ the child's
    # real `find_app_root` finds the tracked `core/.navig` and uses source-tree state.
    # `home/.navig` is the child's NAVIG_CONFIG_DIR, which ends that walk at the global
    # dir. PYTHONPATH keeps `-m navig` importing this checkout from the new cwd.
    (home / ".navig").mkdir(parents=True, exist_ok=True)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if p
    )
    return subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=home,
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


def test_plugin_info_accepts_directory_alias(tmp_path: Path) -> None:
    # The builtin plugin's on-disk dir is `mini_control`; `get()` resolves a
    # plugin by its directory name as well as its canonical id (`mini`).
    result = _run_cli("plugin", "info", "mini_control", home=tmp_path)

    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert "Plugin: mini" in combined
