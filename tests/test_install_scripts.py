"""
Tests for installer scripts (install.ps1 and install.sh).

These tests ensure the scripts pass syntax checks and can execute
a dry-run successfully without throwing errors.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"


def run_cmd(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, **kwargs)


@pytest.fixture(scope="session")
def bash_cmd() -> str | None:
    cmd = shutil.which("bash")
    if cmd and "system32\\bash" in cmd.lower():
        # Prefer Git Bash on Windows to avoid WSL translation bugs
        git_bash = shutil.which("bash", path=r"C:\Program Files\Git\bin")
        return git_bash if git_bash else None
    return cmd


@pytest.fixture(scope="session")
def pwsh_cmd() -> str | None:
    # Prefer pwsh (PowerShell Core), fallback to powershell (Windows PowerShell)
    if cmd := shutil.which("pwsh"):
        return cmd
    if cmd := shutil.which("powershell"):
        return cmd
    return None


def test_install_sh_syntax(bash_cmd):
    """Validate install.sh bash syntax."""
    if not bash_cmd:
        pytest.skip("bash not found")
    assert INSTALL_SH.exists(), "install.sh not found"

    result = run_cmd([bash_cmd, "-n", "install.sh"])
    assert result.returncode == 0, f"Syntax error in install.sh:\n{result.stderr}"


def test_install_sh_dry_run(bash_cmd):
    """Ensure install.sh runs successfully in dry-run mode."""
    if not bash_cmd:
        pytest.skip("bash not found")
    assert INSTALL_SH.exists(), "install.sh not found"

    # Set NAVIG_INSTALL_SH_NO_RUN in case we just want to source it, but here we run it with --dry-run
    result = run_cmd([bash_cmd, "install.sh", "--dry-run"])
    assert result.returncode == 0, (
        f"install.sh --dry-run failed:\n{result.stderr}\n\nSTDOUT:\n{result.stdout}"
    )
    assert "Dry run mode" in result.stdout or "Dry run complete" in result.stdout


def test_install_ps1_dry_run(pwsh_cmd):
    """Ensure install.ps1 runs successfully in dry-run mode."""
    if not pwsh_cmd:
        pytest.skip("powershell not found")
    assert INSTALL_PS1.exists(), "install.ps1 not found"

    # Execute with -DryRun
    result = run_cmd([pwsh_cmd, "-NoProfile", "-NonInteractive", "-File", "install.ps1", "-DryRun"])
    assert result.returncode == 0, (
        f"install.ps1 -DryRun failed:\n{result.stderr}\n\nSTDOUT:\n{result.stdout}"
    )
    combined = result.stdout + result.stderr
    assert "Dry run" in combined, f"Expected 'Dry run' in output, got:\n{combined}"


def test_install_ps1_parse(pwsh_cmd):
    """Validate install.ps1 parses without execution errors (syntax check)."""
    if not pwsh_cmd:
        pytest.skip("powershell not found")
    assert INSTALL_PS1.exists(), "install.ps1 not found"

    # Load the script contents into a scriptblock to catch parse errors
    script_content = INSTALL_PS1.read_text(encoding="utf-8")

    # In PowerShell, invoking [scriptblock]::Create() will throw a ParseException if syntax is invalid
    # We do a tiny inline command to test it
    inline_cmd = f"""
    try {{
        [void][scriptblock]::Create((Get-Content '{INSTALL_PS1}' -Raw))
        exit 0
    }} catch {{
        Write-Error $_.Exception.Message
        exit 1
    }}
    """
    result = run_cmd([pwsh_cmd, "-NoProfile", "-NonInteractive", "-Command", inline_cmd])
    assert result.returncode == 0, f"install.ps1 failed parsing:\n{result.stderr}\n{result.stdout}"


# ---------------------------------------------------------------------------
# Additional coverage: invalid actions, isolation, arg validation
# ---------------------------------------------------------------------------


def test_install_sh_invalid_action_exits_nonzero(bash_cmd):
    """install.sh should exit non-zero on an unknown --action value."""
    if not bash_cmd:
        pytest.skip("bash not found")
    assert INSTALL_SH.exists(), "install.sh not found"

    result = run_cmd([bash_cmd, "install.sh", "--action", "bogus"])
    assert result.returncode != 0, (
        "install.sh should exit non-zero for unknown action\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert any(
        keyword in combined.lower() for keyword in ("unsupported", "unknown", "invalid", "bogus")
    ), f"Expected error context in output, got:\n{combined}"


def test_install_ps1_invalid_action_exits_nonzero(pwsh_cmd):
    """install.ps1 should exit non-zero on an unsupported -Action value."""
    if not pwsh_cmd:
        pytest.skip("powershell not found")
    assert INSTALL_PS1.exists(), "install.ps1 not found"

    result = run_cmd(
        [
            pwsh_cmd,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            "install.ps1",
            "-Action",
            "bogus",
        ]
    )
    assert result.returncode != 0, (
        "install.ps1 should exit non-zero for unknown action\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert any(
        keyword in combined.lower() for keyword in ("unsupported", "unknown", "invalid", "bogus")
    ), f"Expected error context in output, got:\n{combined}"
