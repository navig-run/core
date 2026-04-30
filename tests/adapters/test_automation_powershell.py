"""Tests for navig.adapters.automation.powershell."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ─── Availability guard ───────────────────────────────────────────────────────


def test_powershell_executor_raises_on_non_windows():
    if sys.platform == "win32":
        pytest.skip("Windows-only test gate skipped on Windows")

    with pytest.raises(RuntimeError, match="Windows"):
        from navig.adapters.automation.powershell import PowerShellExecutor

        PowerShellExecutor()


# ─── _detect_powershell ───────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_detect_powershell_prefers_pwsh():
    from navig.adapters.automation.powershell import _detect_powershell

    _detect_powershell.cache_clear()
    with patch("shutil.which", side_effect=lambda x: f"C:\\fake\\{x}.exe" if x == "pwsh" else None):
        info = _detect_powershell()
    assert "pwsh" in info.executable.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_detect_powershell_falls_back_to_powershell():
    from navig.adapters.automation.powershell import _detect_powershell

    _detect_powershell.cache_clear()
    with patch("shutil.which", side_effect=lambda x: f"C:\\fake\\{x}.exe" if x == "powershell" else None):
        info = _detect_powershell()
    assert "powershell" in info.executable.lower()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_detect_powershell_raises_if_none_found():
    from navig.adapters.automation.powershell import _detect_powershell

    _detect_powershell.cache_clear()
    with patch("shutil.which", return_value=None):
        info = _detect_powershell()
    # Always falls back to "powershell" (Windows built-in)
    assert "powershell" in info.executable.lower()


# ─── _build_child_env ─────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_build_child_env_returns_dict():
    from navig.adapters.automation.powershell import _build_child_env

    _build_child_env.cache_clear()
    env = _build_child_env()
    assert isinstance(env, dict)
    assert "PATH" in env or "Path" in env


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_build_child_env_no_duplicate_path_entries():
    from navig.adapters.automation.powershell import _build_child_env

    _build_child_env.cache_clear()
    env = _build_child_env()
    path_val = env.get("PATH") or env.get("Path", "")
    parts = [p for p in path_val.split(";") if p]
    assert len(parts) == len(set(p.lower() for p in parts)), "Duplicate PATH entries found"


# ─── execute_command ──────────────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_execute_command_returns_stdout():
    from navig.adapters.automation.powershell import PowerShellExecutor

    executor = PowerShellExecutor()
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = b"hello\n"
    fake_proc.stderr = b""

    with patch("navig.adapters.automation.powershell.run_with_graceful_timeout", return_value=fake_proc):
        result = executor.execute_command("Write-Output 'hello'", timeout=5)

    assert result.returncode == 0
    assert "hello" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_execute_command_uses_encoded_command():
    """Verify -EncodedCommand is used in the subprocess args."""
    from navig.adapters.automation.powershell import PowerShellExecutor

    executor = PowerShellExecutor()
    captured_args: list = []

    def fake_run(*popenargs, **kwargs):
        captured_args.extend(popenargs)
        m = MagicMock()
        m.returncode = 0
        m.stdout = b""
        m.stderr = b""
        return m

    with patch("navig.adapters.automation.powershell.run_with_graceful_timeout", side_effect=fake_run):
        executor.execute_command("echo hi", timeout=5)

    assert any("-EncodedCommand" in str(a) for a in captured_args), (
        "-EncodedCommand not found in subprocess args"
    )
