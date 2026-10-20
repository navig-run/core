"""The LAN join scripts — parsed by the REAL shells, and checked for the bugs they had.

Regression targets (each seen in a real join on 2026-10-16):
  * the Windows one-liner fetched `/install/windows.ps1`, a route that did not
    exist → every join fell back to `pip --user`, whose Scripts dir is not on PATH;
  * the next `navig config set` launched the 22-step BRAIN wizard on the joining
    laptop (Nerd Font, VS Code/Terminal edits, then a blocking AI-provider prompt);
  * the bash ping posted to the literal string `'$NAVIG_GATEWAY/mesh/ping'`
    (single quotes) — Linux/macOS joins never reached the hub;
  * "✓ NAVIG joined the mesh!" printed whether or not the ping worked;
  * the joiner reported itself as `http://localhost:8789`.
"""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from navig.gateway.routes._install_assets import unix_script, windows_script
from tests.fixtures.posix_shell import POSIX_SHELL, needs_posix_shell

HUB = "o'Brien-PC"  # an apostrophe: the quoting must survive a real hostname
URL = "http://192.0.2.10:8790"
TOKEN = "tok'en$(whoami)`x"  # every metacharacter that could break out of a literal


@pytest.fixture(scope="module")
def win() -> str:
    return windows_script(hub_name=HUB, gateway_url=URL, mesh_token=TOKEN)


@pytest.fixture(scope="module")
def nix() -> str:
    return unix_script(hub_name=HUB, gateway_url=URL, mesh_token=TOKEN)


# ── Windows ──────────────────────────────────────────────────────────────────


def test_windows_script_never_exits_the_users_window(win: str) -> None:
    """It runs via `irm | iex` IN the user's shell — `exit` would close their window."""
    code = [ln for ln in win.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if re.search(r"(^|[;{\s])exit(\s|$)", ln)]
    assert "[Environment]::Exit" not in win
    assert win.splitlines()[2].startswith("& {"), "everything must live in one scriptblock"


def test_windows_script_uses_the_official_installer_with_a_private_profile(win: str) -> None:
    assert "https://navig.run/install.ps1" in win
    # server = daemon + gateway, cloud.enabled false: a LAN device never gets a public URL
    assert "$env:NAVIG_PROFILE = 'server'" in win
    # the official installer exits; it must run as a CHILD process, never via iex,
    # and -NoConfirm keeps the interactive repair menu from blocking a join (seen
    # on a real Windows join: "Select [1-3]" sat waiting mid-flow).
    assert "-File" in win and "Invoke-Expression" not in win
    assert "'-NoConfirm'" in win


def test_windows_script_skips_the_brain_wizard_and_marks_a_node(win: str) -> None:
    assert "$env:NAVIG_SKIP_ONBOARDING = '1'" in win
    assert "config set onboarding.role node" in win


def test_windows_script_restores_the_users_environment(win: str) -> None:
    assert "} finally {" in win
    assert "$savedEnv" in win


def test_windows_script_honours_and_restores_an_explicit_profile(win: str) -> None:
    """The script runs IN the user's shell: it used to force `server` over their
    own NAVIG_PROFILE and then `Remove-Item` it — the choice was silently lost."""
    assert "NAVIG_PROFILE = $env:NAVIG_PROFILE" in win  # saved before we touch it
    assert "if (-not $env:NAVIG_PROFILE) { $env:NAVIG_PROFILE = 'server' }" in win
    assert "Remove-Item Env:\\NAVIG_PROFILE" not in win  # restore comes from $savedEnv


def test_windows_script_reports_its_real_address_and_only_claims_success_on_ok(win: str) -> None:
    assert "UdpClient" in win and '"http://$($myIp):$port"' in win
    assert "$joined = [bool]$resp.ok" in win
    assert "is now part of" in win.split("if ($joined) {", 1)[1].split("} else {", 1)[0]


def test_windows_script_never_prints_the_token(win: str) -> None:
    for line in win.splitlines():
        if re.search(r"\b(Write-Host|Say|Ok|Note|Warn)\b", line):
            assert "$Token" not in line, line


def test_windows_script_shows_live_progress_and_recovers(win: str) -> None:
    """2026-10-17: step 3 sat in a silent 30 s loop, then step 5 fired anyway and
    printed the raw .NET 502. Every wait must tick, and a dead gateway must get
    one clean restart before the script reports failure."""
    assert "waiting for the gateway ·" in win          # per-attempt ticker
    assert "Spin-Start" in win and "Elapsed" in win      # spinner + timing engine
    assert "'service', 'stop'" in win                   # recovery round
    assert "Wait-Gateway 60" in win                     # bounded, visible, longer
    # announce only against an answering gateway; a hub 502 becomes a human sentence
    assert "if (-not $healthy) {" in win
    assert "could not reach this computer" in win


def test_unix_script_shows_live_progress_and_recovers(nix: str) -> None:
    """The bash twin: same ticker, same recovery, no silent 0.75 s loop."""
    assert "waiting for the gateway ·" in nix
    assert "wait_gateway 60" in nix
    assert 'service stop' in nix and 'service start' in nix
    assert "could not reach" in nix


def test_windows_literals_cannot_be_broken_out_of(win: str) -> None:
    assert "$Token   = 'tok''en$(whoami)`x'" in win
    assert "$HubName = 'o''Brien-PC'" in win


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(_powershell() is None, reason="no PowerShell on this host")
def test_windows_script_parses_with_zero_errors(win: str, tmp_path) -> None:
    """The real PowerShell parser — a stray brace or quote fails here, not on a user's laptop."""
    script = tmp_path / "join.ps1"
    script.write_text(win, encoding="utf-8-sig")
    probe = (
        "$errs=$null; $tok=$null; "
        f"[void][System.Management.Automation.Language.Parser]::ParseFile('{script}', [ref]$tok, [ref]$errs); "
        "if ($errs.Count) { $errs | ForEach-Object { $_.Message }; exit 1 } else { 'OK' }"
    )
    r = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr


# ── macOS / Linux ────────────────────────────────────────────────────────────


def test_unix_ping_url_expands(nix: str) -> None:
    """`'$NAVIG_GATEWAY/mesh/ping'` in single quotes posted to a literal string."""
    assert '"$GATEWAY/mesh/ping"' in nix
    assert "'$GATEWAY" not in nix and "'$NAVIG_GATEWAY" not in nix


def test_unix_script_uses_official_installer_private_profile_and_marks_a_node(nix: str) -> None:
    assert "https://navig.run/install.sh | NAVIG_PROFILE=server bash" in nix
    assert "export NAVIG_SKIP_ONBOARDING=1" in nix
    assert "config set onboarding.role node" in nix


def test_unix_script_only_claims_success_on_ok(nix: str) -> None:
    assert "grep -q '\"ok\": *true'" in nix


def test_unix_literals_cannot_be_broken_out_of(nix: str) -> None:
    assert "TOKEN='tok'\\''en$(whoami)`x'" in nix
    assert "HUB='o'\\''Brien-PC'" in nix


@needs_posix_shell
def test_unix_script_parses_with_bash_n(nix: str, tmp_path) -> None:
    script = tmp_path / "join.sh"
    script.write_bytes(nix.encode("utf-8"))  # bytes: no CRLF translation
    r = subprocess.run([POSIX_SHELL, "-n", str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


@needs_posix_shell
def test_unix_literals_round_trip_through_a_real_shell(nix: str, tmp_path) -> None:
    """Evaluate ONLY the literal assignments and echo them back — the exact bytes survive."""
    head = "\n".join(ln for ln in nix.splitlines() if re.match(r"^(GATEWAY|TOKEN|HUB)=", ln))
    script = tmp_path / "vals.sh"
    script.write_bytes((head + '\nprintf "%s|%s|%s" "$GATEWAY" "$TOKEN" "$HUB"\n').encode())
    r = subprocess.run([POSIX_SHELL, str(script)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"{URL}|{TOKEN}|{HUB}"
