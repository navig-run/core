"""
Tests for installer scripts (install.ps1 and install.sh).

These tests ensure the scripts pass syntax checks and can execute
a dry-run successfully without throwing errors.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
INSTALL_PS1 = REPO_ROOT / "install.ps1"


def run_cmd(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    # On Windows the child gets its OWN hidden console. `install.ps1` sets
    # `[Console]::OutputEncoding` (SetConsoleOutputCP), and on an inherited
    # console that change lands on the one console every xdist worker shares and
    # outlives the child. A per-test snapshot/restore cannot fix that: two workers
    # interleave and the later one restores a stale snapshot. Output still flows
    # through the captured pipes.
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", subprocess.CREATE_NO_WINDOW)
    return subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT, **kwargs)


@pytest.fixture(scope="session")
def bash_cmd() -> str | None:
    # Git Bash resolved from git's own install — this used to probe one hardcoded
    # `C:\Program Files\Git\bin` and skipped every test on a scoop-installed git.
    from tests.fixtures.posix_shell import POSIX_SHELL

    return POSIX_SHELL


@pytest.fixture(autouse=True)
def _restore_console_code_page():
    """Belt and braces: put the console code page back after each test.

    `run_cmd` already gives every child its own console, which is the actual fix
    for the cross-worker race; this restore only covers a child spawned another way.

    `install.ps1` runs `[Console]::OutputEncoding = UTF8` — `SetConsoleOutputCP(65001)` on
    the ONE console every xdist worker shares, and it stays after the process exits.
    Measured (2026-09-21): the dry-run tests flipped it 866 → 65001 and a later worker,
    decoding `icacls` with the page it then saw, produced U+FFFD in an unrelated suite. A
    test that changes a process-external resource restores it, the same way `_no_env_leaks`
    guards `os.environ`. No-op off Windows.
    """
    if sys.platform != "win32":
        yield
        return
    import ctypes

    k32 = ctypes.windll.kernel32
    out_cp, in_cp = k32.GetConsoleOutputCP(), k32.GetConsoleCP()
    try:
        yield
    finally:
        if out_cp:
            k32.SetConsoleOutputCP(out_cp)
        if in_cp:
            k32.SetConsoleCP(in_cp)


def test_a_child_code_page_change_never_reaches_the_shared_console(pwsh_cmd):
    """`run_cmd` children must not move the console the test workers share."""
    if sys.platform != "win32":
        pytest.skip("console code pages are Windows-only")
    if not pwsh_cmd:
        pytest.skip("powershell not found")
    import ctypes

    k32 = ctypes.windll.kernel32
    before = k32.GetConsoleOutputCP()
    if not before:
        pytest.skip("this process has no console to observe")
    target = 437 if before == 65001 else 65001
    result = run_cmd(
        [
            pwsh_cmd,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            f"[Console]::OutputEncoding = [Text.Encoding]::GetEncoding({target}); "
            "[Console]::OutputEncoding.CodePage",
        ]
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target), "the child never changed its code page"
    assert k32.GetConsoleOutputCP() == before


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
    INSTALL_PS1.read_text(encoding="utf-8")

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


# ---------------------------------------------------------------------------
# -p/--profile: the Gate 2 installation-profile flag
# ---------------------------------------------------------------------------


def _install_sh_taglines() -> list[str]:
    """The random taglines install.sh picks from — the one line that varies run to run."""
    import re

    src = INSTALL_SH.read_text(encoding="utf-8")
    block = re.search(r"_TAGLINES=\((.*?)\)", src, re.S)
    assert block, "install.sh _TAGLINES block not found — normalizer is out of date"
    return re.findall(r'"([^"]+)"', block.group(1))


def _normalize_dry_run(stdout: str) -> list[str]:
    """stdout without the randomly-chosen tagline line, otherwise untouched."""
    taglines = _install_sh_taglines()
    return [line for line in stdout.splitlines() if not any(t in line for t in taglines)]


def test_install_sh_profile_flag_parses_in_dry_run(bash_cmd):
    """`--dry-run -p server` previews the profile and touches nothing."""
    if not bash_cmd:
        pytest.skip("bash not found")
    result = run_cmd([bash_cmd, "install.sh", "--dry-run", "-p", "server"])
    assert result.returncode == 0, (
        f"dry run with -p must exit 0:\n{result.stderr}\n{result.stdout}"
    )
    assert "Profile" in result.stdout and "server" in result.stdout
    assert "Dry run complete" in result.stdout


def test_install_sh_without_profile_is_unchanged(bash_cmd):
    """No -p → today's output; adding -p adds EXACTLY the one profile row.

    That is the byte-identical default path, asserted from the only surface a
    user sees: strip the randomly-chosen tagline (the sole line that differs
    between any two runs), and the two runs must be line-for-line equal once
    the single Profile row is removed.
    """
    if not bash_cmd:
        pytest.skip("bash not found")
    base = run_cmd([bash_cmd, "install.sh", "--dry-run"])
    with_profile = run_cmd([bash_cmd, "install.sh", "--dry-run", "-p", "server"])
    assert base.returncode == 0 and with_profile.returncode == 0

    base_lines = _normalize_dry_run(base.stdout)
    profile_lines = _normalize_dry_run(with_profile.stdout)

    added = [line for line in profile_lines if line not in base_lines]
    assert len(added) == 1, f"-p must add exactly one row, added: {added}"
    assert "Profile" in added[0] and "server" in added[0], added[0]

    without_profile_row = list(profile_lines)
    without_profile_row.remove(added[0])
    assert without_profile_row == base_lines, (
        "the default (no -p) dry-run path must be unchanged:\n"
        f"only-in-base: {[l for l in base_lines if l not in without_profile_row]}\n"
        f"only-in-profile: {[l for l in without_profile_row if l not in base_lines]}"
    )


def test_install_sh_parse_args_profile_forms(bash_cmd):
    """Parse the flag through sourcing with the entry guard — no installer runs.

    Covers every accepted spelling plus the default and the NAVIG_PROFILE env
    form (the `curl | bash` one-liner passes no argv).
    """
    if not bash_cmd:
        pytest.skip("bash not found")

    probe = (
        "set -e\n"
        "export NAVIG_INSTALL_SH_NO_RUN=1\n"
        "export NAVIG_PROFILE=envprofile\n"
        ". ./install.sh\n"
        "printf 'ENV=[%s]\\n' \"$_PROFILE\"\n"
        "unset NAVIG_PROFILE\n"
        "export NAVIG_INSTALL_SH_NO_RUN=1\n"
        ". ./install.sh\n"
        "printf 'DEFAULT=[%s]\\n' \"$_PROFILE\"\n"
        "_PROFILE=\"\"\n"
        "_parse_args -p server\n"
        "printf 'SHORT=[%s]\\n' \"$_PROFILE\"\n"
        "_PROFILE=\"\"\n"
        "_parse_args --profile minimal\n"
        "printf 'LONG=[%s]\\n' \"$_PROFILE\"\n"
        "_PROFILE=\"\"\n"
        "_parse_args --profile=full\n"
        "printf 'EQ=[%s]\\n' \"$_PROFILE\"\n"
        "_PROFILE=\"sentinel\"\n"
        "_parse_args -y\n"
        "printf 'PRESERVED=[%s]\\n' \"$_PROFILE\"\n"
    )
    result = run_cmd([bash_cmd, "-c", probe])
    assert result.returncode == 0, (
        f"probe failed:\n{result.stderr}\n{result.stdout}"
    )
    out = result.stdout
    assert "ENV=[envprofile]" in out, out
    assert "DEFAULT=[]" in out, out  # no -p and no env → no profile (today's path)
    assert "SHORT=[server]" in out, out
    assert "LONG=[minimal]" in out, out
    assert "EQ=[full]" in out, out
    assert "PRESERVED=[sentinel]" in out, out  # other flags leave _PROFILE alone


def test_install_sh_usage_documents_the_profile_flag(bash_cmd):
    if not bash_cmd:
        pytest.skip("bash not found")
    result = run_cmd([bash_cmd, "install.sh", "-h"])
    assert result.returncode == 0
    assert "-p <name>" in result.stdout
    assert "cli | minimal | server | full" in result.stdout


def test_install_sh_profile_branch_keeps_the_base_daemon_path():
    """Structure guard: without -p the script still reaches register_navig_daemon.

    The behavioural proof is test_install_sh_without_profile_is_unchanged; this
    pins the dispatch itself, which a dry run never executes.
    """
    src = INSTALL_SH.read_text(encoding="utf-8")
    assert 'if [ -n "$_PROFILE" ]; then' in src
    assert "elif register_navig_daemon; then" in src
    # The profile init must live inside the profile branch only.
    profile_branch = src.split('if [ -n "$_PROFILE" ]; then', 1)[1]
    assert 'navig" init --profile "$_PROFILE"' in profile_branch.split("elif", 1)[0]


def test_install_ps1_profile_flag_dry_run(pwsh_cmd):
    """`-Profile server -DryRun` previews the profile and touches nothing."""
    if not pwsh_cmd:
        pytest.skip("powershell not found")
    result = run_cmd(
        [
            pwsh_cmd,
            "-NoProfile",
            "-NonInteractive",
            "-File",
            "install.ps1",
            "-Profile",
            "server",
            "-DryRun",
        ]
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, f"ps1 dry run with -Profile failed:\n{combined}"
    assert "installation profile: server" in combined.lower(), combined
    assert "Dry run complete" in combined


def test_install_ps1_without_profile_has_no_profile_row(pwsh_cmd):
    if not pwsh_cmd:
        pytest.skip("powershell not found")
    result = run_cmd(
        [pwsh_cmd, "-NoProfile", "-NonInteractive", "-File", "install.ps1", "-DryRun"]
    )
    combined = result.stdout + result.stderr
    assert result.returncode == 0, combined
    assert "installation profile" not in combined.lower(), combined
    assert "Dry run complete" in combined
